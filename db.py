import os
import psycopg
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg.connect(dbname=os.getenv("DB_NAME"),
                           user=os.getenv("DB_USER"),
                           password=os.getenv("DB_PASSWORD"),
                           host=os.getenv("DB_HOST", "localhost"),
                           port=os.getenv("DB_PORT", "5432"),
                           autocommit=True)


def create_tables(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS core_news (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            photo_link TEXT,
            news_type VARCHAR(50) NOT NULL,
            news_url TEXT NOT NULL,
            news_position INTEGER NOT NULL
        )
    """)


if __name__ == "__main__":
    with get_connection() as connection:
        create_tables(connection)
    print("Table is ready")
