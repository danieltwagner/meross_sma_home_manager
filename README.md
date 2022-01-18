You must run https://github.com/orlopau/semp and configure it as the SEMP2REST_ORIGIN, e.g. like so:
```
docker run --restart=always -d --net=host -e IP="192.168.0.123" --name semp semp
```

## Docker
```
git clone https://github.com/danieltwagner/meross_sma_home_manager
cd meross_sma_home_manager
docker build -t meross .
docker run -it --rm -e MEROSS_EMAIL="" -e MEROSS_PASSWORD="" -e SEMP2REST_ORIGIN="http://192.168.0.123:9766" --name meross meross
```

## Running locally
```
cp .env.sample .env
pip3 install -r requirements.txt
python3 main.py
```
