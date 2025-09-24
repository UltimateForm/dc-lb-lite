docker stop kov
docker rm kov
docker build . -t kov-img
docker run -d -v ./persist/:/bot/persist/ --name kov kov-img