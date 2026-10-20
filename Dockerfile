# ClaimCrew 管線映像(SPEC-t):Container Apps Job 以託管身分無人值守跑一輪 72 案
# 建置(桌機,不需本機 Docker):az acr build -r acrclaimcrew -t claimcrew:<git sha> --build-arg GIT_SHA=<git sha> .
# 基底以 tag 釘版;2026-10-04 首次 az acr build 解析到的 digest:sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317
FROM python:3.11.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 只放執行需要的東西:程式、prompt、保單條款、合成案件(7.4 MB,與程式碼同版);check/ tests/ docs/ 由 .dockerignore 排除
COPY app ./app
COPY tools ./tools
COPY eval ./eval
COPY data/claims ./data/claims
COPY data/policies ./data/policies

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} CLAIMCREW_AUTH=entra

RUN useradd --create-home --uid 10001 claimcrew && chown -R claimcrew:claimcrew /app
USER claimcrew

ENTRYPOINT ["python", "tools/cloud_run.py"]
