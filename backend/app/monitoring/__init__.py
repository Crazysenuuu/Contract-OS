"""Contract obligation intelligence, automated monitoring & external data
verification (spec 3.15).

Module layout mirrors spec 3.15.3:

    backend/app/monitoring/
    ├── enums.py
    ├── models.py
    ├── schemas.py
    ├── connectors/
    ├── credentials.py
    ├── observations.py
    ├── evaluators/
    ├── service.py
    ├── scheduler.py
    ├── exceptions.py
    ├── router.py
    └── tasks.py

The core invariant: the system must never fabricate an observation when an
external source is unavailable, and provider specifics never leak into
obligation logic (3.15.2).
"""