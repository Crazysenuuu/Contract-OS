"""Integration tests for i18n endpoints.

The TranslationService was converted from the legacy sync ``db.query()`` API
(which crashed on the injected AsyncSession) to SQLAlchemy 2.0 async — these
tests pin the real behavior of the language/translation/locale endpoints.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient


pytestmark = pytest.mark.integration


@pytest.mark.integration
class TestI18nIntegration:
    """i18n endpoints must work against a real async session."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient, auth_headers, test_org):
        self.client = client
        self.headers = auth_headers

    async def test_list_languages_is_public(self):
        """The language list is public (locale picker on the login page)."""
        response = await self.client.get("/api/v1/i18n/languages")
        assert response.status_code == 200

    async def test_list_languages(self):
        # Seed default languages through the API first (idempotent)
        seed = await self.client.post(
            "/api/v1/i18n/languages/seed", headers=self.headers
        )
        assert seed.status_code == 200

        resp = await self.client.get(
            "/api/v1/i18n/languages", headers=self.headers
        )
        assert resp.status_code == 200
        languages = resp.json()
        assert isinstance(languages, list)
        codes = {lang["code"] for lang in languages}
        assert "en" in codes

    async def test_get_language_detail(self):
        await self.client.post("/api/v1/i18n/languages/seed", headers=self.headers)
        resp = await self.client.get(
            "/api/v1/i18n/languages/en", headers=self.headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "en"

    async def test_get_language_404(self):
        resp = await self.client.get(
            "/api/v1/i18n/languages/xx", headers=self.headers
        )
        assert resp.status_code == 404

    async def test_get_translations_namespace(self):
        resp = await self.client.get(
            "/api/v1/i18n/translations/ui",
            params={"language": "en"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["namespace"] == "ui"
        assert isinstance(body["translations"], dict)

    async def test_set_translation_creates_draft(self):
        """New translations are stored as drafts, hidden from published reads."""
        await self.client.post("/api/v1/i18n/languages/seed", headers=self.headers)
        payload = {
            "namespace": "test",
            "key": "draft_key",
            "language_code": "en",
            "value": "Draft Value",
        }
        resp = await self.client.post(
            "/api/v1/i18n/translations", json=payload, headers=self.headers
        )
        assert resp.status_code == 200
        created = resp.json()
        assert created["key"] == "draft_key"
        assert created["status"] == "draft"

        # Published-only reads must not return the draft
        resp = await self.client.get(
            "/api/v1/i18n/translations/test/draft_key",
            params={"language": "en"},
            headers=self.headers,
        )
        assert resp.status_code == 404

    async def test_seed_ui_translations_then_read(self):
        """Seeded UI translations are published and readable."""
        await self.client.post("/api/v1/i18n/languages/seed", headers=self.headers)
        seed = await self.client.post(
            "/api/v1/i18n/translations/seed", headers=self.headers
        )
        assert seed.status_code == 200

        resp = await self.client.get(
            "/api/v1/i18n/translations/ui",
            params={"language": "en"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        translations = resp.json()["translations"]
        assert translations.get("app_name") == "ContractOS"

    async def test_detect_language(self):
        resp = await self.client.get(
            "/api/v1/i18n/detect-language",
            params={"text": "ආයුබෝවන්"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert resp.json()["detected_language"] == "si"

    async def test_document_locale_defaults(self):
        resp = await self.client.get(
            "/api/v1/i18n/documents/nonexistent-agreement/locale",
            headers=self.headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is False
        assert body["defaults"]["primary_language"] == "en"

    async def test_glossary(self):
        resp = await self.client.get(
            "/api/v1/i18n/glossary", params={"language": "en"}, headers=self.headers
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
