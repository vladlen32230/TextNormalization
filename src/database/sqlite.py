from sqlalchemy import Column, Integer, String, JSON, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from contextlib import contextmanager
from typing import Generator

engine = create_engine("sqlite:///database.db")
Sessionmaker = sessionmaker(bind=engine)

@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = Sessionmaker()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    else:
        session.commit()
    finally:
        session.close()



Base = declarative_base()

class Example(Base):
    __tablename__ = "examples"
    
    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)
    unnormalized_text = Column(String, nullable=False)
    normalized_json = Column(JSON, nullable=False)

class Schema(Base):
    __tablename__ = "schemas"

    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False, unique=True)
    attributes = Column(JSON, nullable=False)