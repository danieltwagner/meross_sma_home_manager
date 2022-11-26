FROM python:3.11-alpine

RUN apk update && apk add --no-cache gcc g++ libffi-dev

WORKDIR /usr/src/app
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

CMD [ "python3", "./main.py" ]
