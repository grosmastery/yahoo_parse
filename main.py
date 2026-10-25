import base64
from datetime import datetime
import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import ssl
import time
import traceback
import requests
from selenium import webdriver
from selenium.common import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.wait import WebDriverWait
from dotenv import load_dotenv
from db import get_connection, create_tables

load_dotenv()

HERO = "//div[@data-testid='hero-v2-container']"


class Post:

    def __init__(self):
        os.makedirs('screenshot', exist_ok=True)
        self.start_browser()
        self.screenshot_path = None

        self.db = get_connection()
        create_tables(self.db)
        self.first_run = self.db.execute("SELECT COUNT(*) FROM core_news").fetchone()[0] == 0

    def start_browser(self):
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.page_load_strategy = "eager"
        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.set_page_load_timeout(60)

    def restart_browser_if_dead(self):
        try:
            self.driver.title
        except Exception:
            print("Browser is not responding, starting a new one")
            self.driver.service.stop()
            self.start_browser()

    def open_page(self):
        self.driver.get("https://finance.yahoo.com/")
        self.close_consent()
        WebDriverWait(self.driver, 20).until(
            ec.presence_of_element_located((By.XPATH, f"{HERO}//div[contains(@class, 'lead')]//h3")))
        self.driver.execute_script("window.stop();")

    def get_news(self):
        news = []

        lead = self.driver.find_element(By.XPATH, f"{HERO}/div[contains(@class, 'lead')]")
        columns = self.driver.find_elements(By.XPATH, f"{HERO}/div[contains(@class, 'story-col')]")

        sections = [(lead, "main")]
        for column in columns:
            sections.append((column, column.find_element(By.XPATH, ".//h2").get_property('textContent').strip()))

        for section, news_type in sections:
            for item in section.find_elements(By.XPATH, ".//li | .//div[contains(@class, 'lead-story')]"):
                title = item.find_elements(By.XPATH, ".//h3")
                url = item.find_elements(By.XPATH, ".//h3/ancestor::a")
                img = item.find_elements(By.XPATH, ".//img")
                if not title or not url:
                    continue

                url = url[0].get_attribute('href').split('?')[0]
                if url in [n['url'] for n in news]:
                    continue

                news.append({'title': title[0].get_property('textContent').strip(),
                             'url': url,
                             'img': img[0].get_attribute('src') if img else None,
                             'news_type': news_type,
                             'position': len(news) + 1})
        return news

    def parse_news(self):
        screenshot_taken = False
        keywords = self.get_keywords()
        changed_news_type = []

        for post in self.get_news():
            last = self.db.execute("SELECT news_position FROM core_news WHERE news_url = %s ORDER BY id DESC LIMIT 1",
                                   (post['url'],)).fetchone()
            if last and last[0] == post['position']:
                continue

            self.db.execute("INSERT INTO core_news (title, photo_link, news_type, news_url, news_position) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (post['title'][:255], post['img'], post['news_type'], post['url'], post['position']))

            if self.first_run:
                continue

            if not last or post['position'] < last[0]:
                if post['news_type'] == "main":
                    boolean = True
                else:
                    boolean = any(key in post['title'].lower() for key in keywords)
                changed_news_type.append([post['news_type'], post['url'], boolean])
                if not screenshot_taken:
                    self.take_screenshot()
                    screenshot_taken = True

        self.first_run = False
        return changed_news_type

    def get_keywords(self):
        if not os.path.exists('keywords.txt'):
            return []
        with open('keywords.txt', encoding='utf-8') as file:
            return [line.strip().lower() for line in file if line.strip() and not line.startswith('#')]

    def send_main(self, changed_news_type):
        sender_email = os.getenv("EMAIL_ADDRESS")
        receiver_email_main = os.getenv("EMAIL_ADDRESS_MAIN")
        receiver_email_other = os.getenv("EMAIL_ADDRESS_OTHER")
        password = os.getenv("EMAIL_PASS")
        if not sender_email or not password:
            return

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender_email, password)

            for news_type, url, boolean in changed_news_type:
                receiver_email = receiver_email_main if boolean else receiver_email_other
                message = MIMEMultipart("alternative")
                message["Subject"] = news_type
                message["From"] = sender_email
                message["To"] = receiver_email
                message.attach(MIMEText(url, 'plain'))

                with open(self.screenshot_path, 'rb') as file:
                    mime = MIMEBase('image', 'png', filename='img.png')
                    mime.add_header('Content-Disposition', 'attachment', filename='img1.png')
                    mime.add_header('X-Attachment-Id', '0')
                    mime.add_header('Content-ID', '<0>')
                    mime.set_payload(file.read())
                    encoders.encode_base64(mime)
                    message.attach(mime)

                server.sendmail(sender_email, receiver_email, message.as_string())

    def send_telegram_message(self, messages):
        bot_token = os.getenv("BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_IDS")
        if not bot_token:
            return

        with open(self.screenshot_path, 'rb') as file:
            photo = file.read()

        url = f'https://api.telegram.org/bot{bot_token}/sendPhoto'
        for message, news_url, _ in messages:
            response = requests.post(url, data={'chat_id': chat_id, 'caption': f'{message}: {news_url}'}, files={'photo': ('img.png', photo)})
            if not response.ok:
                print(f"Telegram error: {response.text}")

    def take_screenshot(self):
        t = str(datetime.now().strftime("%y-%m-%d_%H-%M-%S"))
        ele = self.driver.find_element(By.XPATH, HERO)
        x, y, width, height = self.driver.execute_script(
            "const r = arguments[0].getBoundingClientRect(); return [r.x, r.y + window.scrollY, r.width, r.height];", ele)
        shot = self.driver.execute_cdp_cmd("Page.captureScreenshot", {
            "captureBeyondViewport": True,
            "clip": {"x": x, "y": y, "width": width, "height": height, "scale": 1}})
        self.screenshot_path = f'screenshot/created_at_{t}.png'
        with open(self.screenshot_path, 'wb') as file:
            file.write(base64.b64decode(shot['data']))

    def close_consent(self):
        if "consent." not in self.driver.current_url:
            return
        try:
            WebDriverWait(self.driver, 10).until(
                ec.element_to_be_clickable((By.XPATH, "//button[@name='reject']"))).click()
        except TimeoutException:
            pass

    def main(self):
        while True:
            try:
                self.open_page()
                changed_news_type = self.parse_news()
                if changed_news_type:
                    print(datetime.now(), changed_news_type)
                    self.send_main(changed_news_type)
                    self.send_telegram_message(changed_news_type)
            except Exception:
                traceback.print_exc()
                self.restart_browser_if_dead()
            time.sleep(15)


if __name__ == "__main__":
    Post().main()
