"""Demo Lambda that fails in controllable ways for Flare testing."""

import json
import os


def handler(event, context):
    mode = event.get("mode", os.environ.get("FAILURE_MODE", "exception"))

    if mode == "exception":
        raise RuntimeError(
            "Unhandled exception in payment processing: "
            "NoneType has no attribute 'charge_id'"
        )

    if mode == "oom":
        # Allocate until Lambda runs out of memory
        data = []
        while True:
            data.append(b"x" * (1024 * 1024 * 100))

    if mode == "timeout":
        import time

        time.sleep(900)

    if mode == "mixed":
        import logging

        logger = logging.getLogger()
        for i in range(50):
            logger.info(f"Processing batch {i}: status=ok records=1024")

        for i in range(5):
            logger.error(f"Connection refused to db-primary.internal:5432 attempt={i}")
        logger.warning("Falling back to read replica db-replica.internal:5432")

        for i in range(20):
            logger.info(f"Processing batch {50 + i}: status=ok records=1024")

        raise ConnectionError("Lost connection to db-primary.internal:5432")

    return {"statusCode": 200, "body": json.dumps({"mode": mode, "status": "ok"})}
