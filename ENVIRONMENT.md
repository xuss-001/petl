Native validation: Linux, Python 3.14; core tests use pytest 8.x and pytest-cov.
Install requirements-tests.txt in an external virtual environment.
Run python -m pytest petl/test/transform petl/test/util and the relevant local IO tests.
Optional format and external database integrations require their corresponding extras.
No external service is needed for core transforms, JSON or SQLite.
Do not commit local credentials or machine configuration.
