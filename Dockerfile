FROM python:3.11-slim
LABEL maintainer="Anthony Farina <anthony.farina@computacenter.com>"

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD [ "python", "src/ServiceNow-Warranty-Updater.py" ]
