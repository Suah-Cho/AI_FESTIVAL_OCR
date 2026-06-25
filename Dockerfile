FROM python:3.11-slim

WORKDIR /app

# opencv-python-headless 는 manylinux wheel 에 런타임을 포함해
# apt 로 Debian 패키지를 받지 않아도 된다 (사내망에서 apt 미러 차단 시 유리).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
