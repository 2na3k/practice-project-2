from contextlib import asynccontextmanager

from fastapi import FastAPI

from .cache import connect_cache
from .routes import router
from .users import init_user_database, load_username_filter, open_user_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_user_database()
    connection = open_user_database()
    try:
        app.state.usernames = load_username_filter(connection)
    finally:
        connection.close()
    app.state.cache = connect_cache()
    try:
        yield
    finally:
        app.state.cache.close()


app = FastAPI(title="Remymy Recipe API", version="1.0.0", lifespan=lifespan)
app.include_router(router)
