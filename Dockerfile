FROM python:3.12-slim
WORKDIR /app
COPY requirements-mcp.txt .
ENV PIP_INDEX_URL=https://pypi.sankuai.com/simple
RUN pip install -r requirements-mcp.txt --no-cache-dir --retries 10
COPY backend/db_parser.py backend/mcp_service.py ./
EXPOSE 8000
CMD ["python", "mcp_service.py"]