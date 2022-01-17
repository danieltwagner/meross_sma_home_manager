You must run https://github.com/orlopau/semp and configure it as the SEMP2REST_ORIGIN

## Docker
```
git clone https://github.com/danieltwagner/meross_sma_home_manager
cd meross_sma_home_manager
docker build -t meross .
docker run -it --rm --name meross meross
```

## Running locally
```
cp .env.sample .env
pip3 install -r requirements.txt
python3 main.py
```
