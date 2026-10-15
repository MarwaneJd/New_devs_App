"""Read-only smoke test of running services using both assignment accounts."""
import concurrent.futures
import os

import requests

BASE = os.getenv("API_BASE", "http://localhost:8000") + "/api/v1"


def login(name, password):
    response = requests.post(BASE + "/auth/login", json={
        "email": name + "@propertyflow.com", "password": password,
    }, timeout=10)
    response.raise_for_status()
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def summary(headers, property_id, **filters):
    response = requests.get(BASE + "/dashboard/summary", headers=headers,
                            params={"property_id": property_id, **filters}, timeout=10)
    response.raise_for_status()
    return response.json()


def main():
    sunset = login("sunset", "client_a_2024")
    ocean = login("ocean", "client_b_2024")
    expected = [
        (sunset, "prop-001", "2250.00", 4),
        (sunset, "prop-002", "4975.50", 4),
        (sunset, "prop-003", "6100.50", 2),
        (ocean, "prop-001", "0.00", 0),
        (ocean, "prop-004", "1776.50", 4),
        (ocean, "prop-005", "3256.00", 3),
    ]
    for headers, prop, total, count in expected:
        for filters in [{}, {"year": 2024}, {"year": 2024, "month": 3}]:
            data = summary(headers, prop, **filters)
            assert (data["total_revenue"], data["reservations_count"]) == (total, count), data
        tenant = "tenant-a" if headers is sunset else "tenant-b"
        print(f"PASS {tenant} {prop}: USD {total}, {count} bookings")
    assert summary(sunset, "prop-001", month=2, year=2024)["total_revenue"] == "0.00"

    for headers, foreign_property, own_name in [
        (sunset, "prop-004", "Beach House Alpha"),
        (ocean, "prop-002", "Mountain Lodge Beta"),
    ]:
        response = requests.get(BASE + "/dashboard/summary", headers=headers,
                                params={"property_id": foreign_property}, timeout=10)
        assert response.status_code == 404, response.text
        properties = requests.get(BASE + "/dashboard/properties", headers=headers, timeout=10)
        properties.raise_for_status()
        assert len(properties.json()) == 3
        assert properties.json()[0]["name"] == own_name

    def refresh(item):
        headers, _, total, count = item
        data = summary(headers, "prop-001", month=3, year=2024)
        assert (data["total_revenue"], data["reservations_count"]) == (total, count)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(refresh, [expected[0], expected[3]] * 10))
    print("PASS tenant-scoped dropdowns, foreign-property denial, February exclusion, and 20 concurrent refreshes")


if __name__ == "__main__":
    main()
