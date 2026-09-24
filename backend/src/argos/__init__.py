def main() -> None:
    import uvicorn

    from argos.config import settings

    uvicorn.run("argos.main:app", host=settings.host, port=settings.port)
