# Reverse-stock-split-Search
Similar to my other repo this tool searches for Reverse Stock Splits that have a likely chance of rounding up after a split. This version of the tool utilizes ai to get more accurate results. 

## DISCLAIMER
I am not a financial advisor and not affiliated with any of the brokerages listed below. Use this tool at your own risk. I am not responsible for any losses or damages you may incur by using this project. This tool is provided as-is with no warranty.

### Info
Please leave a star if you find this tool useful\
This tool costs less than 10 cents a month in Open AI tokens. You need to add an AI api key or else the bot will not work (In the future I may make it work without ai if the results are consistent)\
Im sorry for the mess of files and unorganized code, things like the dataset.csv file were originally meant for me being able to train my own LM but I eventually realized its useless but some code is dependant on the information now,so I never removed it.
If you have any issues let me know my updated contact information is in my github bio.\
I use this searching tool with the auto-rsa bot by [@NelsonDane](https://github.com/NelsonDane/auto-rsa)

### Requirements:
Add all necessary information in secrets.json\
Install python \
Install docker

### Guide:

```bash
# Clone the repository
git clone https://github.com/LJK2git/Reverse-stock-split-Search.git

# Enter the project folder
cd Reverse-Stock-Split-Searcher
```
Setup for docker
```bash
docker compose up -d --build
```
If you update any config files run docker-compose up -d --build to restart
