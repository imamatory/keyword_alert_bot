#!/bin/bash

envsubst < config.yml.example > config.yml
python main.py