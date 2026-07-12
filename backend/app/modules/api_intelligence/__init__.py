"""Module 3 — Universal API Intelligence.

Discovers APIs two ways: passively capturing the XHR/fetch/GraphQL traffic a live
website makes (Playwright network capture during load + light navigation), and
statically extracting route definitions from a repository (FastAPI, Flask, Django,
Express, Next.js, Spring, Gin). Generates OpenAPI 3.0 and Postman collection artifacts.
"""
from app.modules.api_intelligence.module import APIIntelligenceModule

MODULE = APIIntelligenceModule
