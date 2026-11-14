"""API tests for /regulations. Every write is rolled back (see the `api` fixture)."""

import uuid

import httpx
import pytest

pytestmark = pytest.mark.anyio


def new_payload(**overrides: object) -> dict[str, object]:
    # A unique code per test, so tests never collide with each other or with seed data.
    payload: dict[str, object] = {
        "code": f"TEST-{uuid.uuid4().hex[:8].upper()}",
        "authority": "SAMA",
        "title_en": "Test Regulation (fictional)",
        "title_ar": "لائحة اختبار (افتراضية)",
        "effective_date": "2025-01-01",
        "source_url": "https://example.com/regulation",
        "articles": [
            {"article_number": "2", "language": "en", "position": 2, "body": "Second."},
            {"article_number": "1", "language": "en", "position": 1, "body": "First."},
            {"article_number": "1", "language": "ar", "position": 1, "body": "الأولى."},
        ],
    }
    payload.update(overrides)
    return payload


async def test_create_returns_201_with_sorted_articles(api: httpx.AsyncClient) -> None:
    payload = new_payload()

    response = await api.post("/regulations", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == payload["code"]
    assert body["status"] == "active"  # default applied
    assert body["source_url"] == "https://example.com/regulation"
    assert [a["position"] for a in body["articles"]] == [1, 1, 2]


async def test_code_is_normalised_to_upper_case(api: httpx.AsyncClient) -> None:
    code = f"test-{uuid.uuid4().hex[:6]}"

    response = await api.post("/regulations", json=new_payload(code=f"  {code}  "))

    assert response.status_code == 201
    assert response.json()["code"] == code.upper()


async def test_duplicate_code_returns_409(api: httpx.AsyncClient) -> None:
    payload = new_payload()
    assert (await api.post("/regulations", json=payload)).status_code == 201

    response = await api.post("/regulations", json=payload)

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


@pytest.mark.parametrize(
    ("overrides", "bad_field"),
    [
        ({"code": "has spaces!"}, "code"),
        ({"authority": "FBI"}, "authority"),
        ({"title_en": "x"}, "title_en"),
        ({"source_url": "not-a-url"}, "source_url"),
        ({"is_admin": True}, "is_admin"),  # unknown fields are rejected (extra="forbid")
    ],
)
async def test_invalid_input_returns_422(
    api: httpx.AsyncClient, overrides: dict[str, object], bad_field: str
) -> None:
    # parametrize: the same test runs once per row above, each reported separately.
    response = await api.post("/regulations", json=new_payload(**overrides))

    assert response.status_code == 422
    assert bad_field in [error["loc"][-1] for error in response.json()["detail"]]


async def test_duplicate_article_in_payload_returns_422(api: httpx.AsyncClient) -> None:
    article = {"article_number": "1", "language": "en", "position": 1, "body": "Text."}

    response = await api.post("/regulations", json=new_payload(articles=[article, article]))

    assert response.status_code == 422
    assert "Duplicate article 1 (en)" in response.text


async def test_get_by_code_is_case_insensitive(api: httpx.AsyncClient) -> None:
    payload = new_payload()
    await api.post("/regulations", json=payload)

    response = await api.get(f"/regulations/{str(payload['code']).lower()}")

    assert response.status_code == 200
    assert len(response.json()["articles"]) == 3


async def test_get_unknown_code_returns_404(api: httpx.AsyncClient) -> None:
    response = await api.get("/regulations/DOES-NOT-EXIST")

    assert response.status_code == 404


async def test_list_filters_and_paginates(api: httpx.AsyncClient) -> None:
    # A unique marker in every code lets us search for only this test's rows.
    marker = uuid.uuid4().hex[:6].upper()
    for i, authority in enumerate(["SAMA", "SAMA", "ZATCA"]):
        response = await api.post(
            "/regulations",
            json=new_payload(code=f"T{marker}-{i}", authority=authority, articles=[]),
        )
        assert response.status_code == 201

    first_page = (await api.get("/regulations", params={"q": marker, "limit": 2})).json()
    second_page = (
        await api.get("/regulations", params={"q": marker, "limit": 2, "offset": 2})
    ).json()
    only_sama = (await api.get("/regulations", params={"q": marker, "authority": "SAMA"})).json()

    assert first_page["total"] == 3
    assert [r["code"] for r in first_page["items"]] == [f"T{marker}-0", f"T{marker}-1"]
    assert [r["code"] for r in second_page["items"]] == [f"T{marker}-2"]
    assert only_sama["total"] == 2
    assert "articles" not in first_page["items"][0]  # list view is the light summary


async def test_list_rejects_limit_over_100(api: httpx.AsyncClient) -> None:
    response = await api.get("/regulations", params={"limit": 1000})

    assert response.status_code == 422
