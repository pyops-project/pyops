import os.path

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

db_path = os.path.join(os.path.dirname(__file__), "../../data/app.db")

# https://docs.sqlalchemy.org/en/14/orm/quickstart.html

SQLALCHEMY_DATABASE_URL = "sqlite:///{}".format(db_path)
# SQLALCHEMY_DATABASE_URL = "postgresql://user:password@postgresserver/db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False},
                             future=True
                             
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()