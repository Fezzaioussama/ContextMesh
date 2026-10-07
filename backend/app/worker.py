"""`python -m app.worker` entry point; bootstrap owns worker composition."""

from app.bootstrap.worker import main

if __name__ == "__main__":
    main()
