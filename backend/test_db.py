from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError


def test_connection(url):
    try:
        engine = create_engine(url)
        connection = engine.connect()
        print("✅ Connection successful!, url is:", url)
        connection.close()
    except SQLAlchemyError as e:
        print(f"❌ Error connecting to database: {e}")


if __name__ == "__main__":
    database_url = "sqlite:///./open_webui.db"
    test_connection(database_url)
