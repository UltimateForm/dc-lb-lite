docker stop dc-lb-lite
docker rm dc-lb-lite
docker build . -t dc-lb-lite-img
docker run -d -v ./persist/:/bot/persist/ --name dc-lb-lite dc-lb-lite-img