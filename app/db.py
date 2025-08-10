from typing import Annotated

from fastapi import Depends
from sqlmodel import Session, SQLModel, create_engine, Field

from enum import Enum

sqlite_file_name = "artifact.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args)


class ActionType(str, Enum):
    create_character = "create_character"
    create_custom_character = "create_custom_character"
    delete_character = "delete_character"
    move = "move"
    equip_item = "equip_item"
    unequip_item = "unequip_item"
    fight = "fight"
    gather = "gather"
    craft = "craft"
    delete_item = "delete_item"
    give_item = "give_item"
    buy_item = "buy_item"
   
class CharacterLog(SQLModel, table=True):
    id: Annotated[int | None, Field(default=None, primary_key=True)]
    character_name: Annotated[str, Field(description="character name", index=True)]
    action_type: Annotated[ActionType, Field(description="action type", index=True)]
    log: Annotated[str, Field(description="log of the performed action")]


def init_db():
    """
    Initializes the SQLite database.
    """
    SQLModel.metadata.create_all(engine)


def rm_db():
    """
    Deletes the SQLite database.
    """
    SQLModel.metadata.drop_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
