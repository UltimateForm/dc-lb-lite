FROM python:3.11.3

WORKDIR /bot

COPY ./Pipfile .

COPY ./.env* .
     
RUN pip install pipenv

RUN pipenv install

COPY ./parsers/* ./parsers

COPY ./models/* ./models

COPY ./main.py ./

CMD ["pipenv", "run", "python", "main.py"]