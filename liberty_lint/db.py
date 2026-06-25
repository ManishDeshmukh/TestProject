from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base


def make_engine(db_url: str = "sqlite:///liberty.db"):
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    return engine


def make_session(db_url: str = "sqlite:///liberty.db"):
    engine = make_engine(db_url)
    return sessionmaker(bind=engine)()
