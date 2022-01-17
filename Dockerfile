FROM python:3.10-slim-buster

RUN apt-get update && apt-get install --no-install-recommends -y gcc g++ libffi-dev libstdc++-8-dev

WORKDIR /usr/src/app
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

CMD [ "python3", "./main.py" ]
