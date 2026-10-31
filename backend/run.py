"""Entry point: run with `python run.py` to start the NeverWrite API server."""
import uvicorn

if __name__ == "__main__":
    # Localhost only: the API has no auth and serves personal handwriting data
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
