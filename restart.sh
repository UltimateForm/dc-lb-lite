docker stop rbb
docker rm rbb
docker build . -t rbb-img
docker run -d -v ./persist/:/bot/persist/ --name rbb rbb-img