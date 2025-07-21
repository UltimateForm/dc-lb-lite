FROM python:3.11.3

WORKDIR /bot

COPY ./Pipfile .

COPY ./.env* .
     
RUN pip install pipenv

RUN pipenv install

COPY ./parsers/ ./parsers/

COPY ./models/ ./models/

COPY ./common/ ./common/

COPY ./rcon/ ./rcon/

COPY ./score_tracker/ ./score_tracker/

COPY ./main.py ./

COPY ./compute/ ./compute/

CMD ["pipenv", "run", "python", "main.py"]