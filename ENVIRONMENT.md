Native validation: Linux, CPython 3.12.13. No external service is needed for core transforms, JSON or SQLite.
Create an external virtual environment; install the pinned packages in requirements-native.txt.
Run python setup.py --version once to generate the ignored petl/version.py using setuptools-scm.
Run python -m pytest petl/test/transform petl/test/util and the relevant local IO tests.
Optional format and external database integrations require their corresponding extras.
Do not commit local credentials or machine configuration.
