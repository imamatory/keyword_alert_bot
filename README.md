
# 🤖Telegram keyword alert bot⏰

![Build Status](https://github.com/Hootrix/keyword_alert_bot/workflows/CI/CD%20Pipeline/badge.svg)
[![Python](https://img.shields.io/badge/python-3.7%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/github/license/Hootrix/keyword_alert_bot)](https://github.com/Hootrix/keyword_alert_bot/blob/master/LICENSE)
[![Paypal Donate](https://img.shields.io/badge/Paypal%20Donate-yellow?style=flat&logo=paypal)](https://www.paypal.com/donate/?business=DRVVDHMVL8G7Q&no_recurring=0&item_name=Sponsored+development+of+keyword_alert_bot%21+&currency_code=USD)
[![Github Sponsor](https://img.shields.io/badge/Github%20Sponsor-yellow?style=flat&logo=github)](https://github.com/sponsors/Hootrix)

Telegram keyword reminder robot is used to monitor keyword messages in channels/groups in real time.

Ensure that ordinary Telegram accounts can join designated groups without verification.

Warning: Demo bot is overloaded, it is recommended to use Docker image self-deployment


👉  Features：

- <input checked="" disabled="" type="checkbox"> Keyword message subscription: push message reminders in real time based on set keywords and channels 
- <input checked="" disabled="" type="checkbox"> Supports regular expression matching syntax 
- <input checked="" disabled="" type="checkbox"> Supports multi-channel subscription & multi-keyword subscription 
- <input checked="" disabled="" type="checkbox"> Support subscribing to group messages 
- <input checked="" disabled="" type="checkbox"> Support message subscription of private channel ID/invitation link 
- <input checked="" disabled="" type="checkbox"> Support private group subscription 

  1. https://t.me/+B8yv7lgd9FI0Y2M1  
  2. https://t.me/joinchat/B8yv7lgd9FI0Y2M1 
  

👉 Todo:

- <input disabled="" type="checkbox"> Private channel message reminder full content preview 
- <input disabled="" type="checkbox"> Multiple account support 
- <input disabled="" type="checkbox"> Scan to exit useless channels/groups 

## 🔍Demo

http://t.me/keyword_alert_bot

<img width="250px" alt="demo" src="https://user-images.githubusercontent.com/10736915/171514829-4186d486-e1f4-4303-b3a9-1cfc1b571668.png" />


## 🚀Run

### 1. Configuration file


#### config.yml.example --> config.yml

Copy config.yml.example to local and rename it to config.yml, and then configure it according to the api applied below

#### Create Telelgram Account & API

It is recommended to use a new Telegram account[Open api](https://my.telegram.org/apps)to use

#### Create BOT 

[https://t.me/BotFather](https://t.me/BotFather)Create a bot


### 2. 🐳Docker

```
$ docker run -it --name keyword_alert_bot -v $(pwd)/config.yml:/app/config.yml -v $(pwd)/db/:/app/db/ yha8897/keyword_alert_bot

Please enter the code you received: 12345
Please enter your password: 
Signed in successfully as DEMO; remember to not break the ToS or you will risk an account ban!

#################################################################
##                                                             ##
##                          ● success                          ##
##   🤖️Telegram keyword alert bot (Version: 20240627.f6672cf)  ##
##                                                             ##
#################################################################

```

The first run requires a Telegram account to receive a digital verification code and enter a password (triggered by Telegram API), and then success will be prompted.


other
```
# 重启
$ docker restart keyword_alert_bot

# 停止
$ docker stop keyword_alert_bot

# Database file mounting path: /app/db/.db

$ docker run -it --name keyword_alert_bot  -v $(pwd)/config.yml:/app/config.yml -v $(pwd)/db/keyword_alert_bot.db:/app/db/.db yha8897/keyword_alert_bot

```

### docker镜像更新

To avoid data loss, remember to back up the data in docker before updating the container. If the database file has been mounted into the container, it is not necessary.
```
$ docker cp keyword_alert_bot:/app/db/.db ~/keyword_alert_bot.db
# You can save it to: ~/keyword_alert_bot.db

```

Persist all data to avoid permission issues`--user root`Force root permission execution
```
$ docker run -d --name keyword_alert_bot --user root  -v $(pwd)/config.yml:/app/config.yml -v $(pwd)/db/:/app/db/ -v $(pwd)/.tmp/:/app/.tmp/ -v $(pwd)/logs/:/app/logs/  yha8897/keyword_alert_bot
```

## 💪Manual Build

运行环境 python3.7+


```
$ pipenv install

$ pipenv shell

$ python3 ./main.py
```


## 📘Usage

### Normal keyword matching


```
/subscribe   免费[https://t.me/tianfutong](https://t.me/tianfutong)
/subscribe   优惠券[https://t.me/tianfutong](https://t.me/tianfutong)

```

### Regular expression matching


Use regular grammar rules similar to JavaScript and wrap regular statements with /. Currently available matching patterns: i, g

```
# 订阅手机型号关键字：iphone x，排除XR，XS等型号，且忽略大小写
/subscribe   /(iphone\s*x)(?:[^sr]|$)/ig  com9ji,xiaobaiup
/subscribe   /(iphone\s*x)(?:[^sr]|$)/ig  https://t.me/com9ji,https://t.me/xiaobaiup

# xx券
/subscribe  /([\S]{2}券)/g  https://t.me/tianfutong

```



## Q & A

> Bug Feedback: https://github.com/Hootrix/keyword_alert_bot/issues


 ### 1. You have joined too many channels/supergroups (caused by JoinChannelRequest)

The total number of all subscribed channels in the BOT exceeds 500. The reason is due to the limitations of the Telegram demo account used by BOT. It is recommended that you deploy it yourself

 ### 2. sqlite3.OperationalError: unable to open database file

If the docker image is started, since the nonroot account is used internally, you need to authorize the permission to mount the file or use it directly.`--user root`parameter
  ```
  $ docker run -it --name keyword_alert_bot --user root  -v $(pwd)/config.yml:/app/config.yml -v $(pwd)/db/:/app/db/ -v $(pwd)/.tmp/:/app/.tmp/ -v $(pwd)/logs/:/app/logs/  yha8897/keyword_alert_bot
  ```


### 3. Check the log and find that some groups cannot receive messages, but the software client receives them normally.


 🤔尝试更新telethon到最新版本或者稳定的1.24.0版本

 ### 4. 订阅群组消息，机器人没任何反应
 https://github.com/Hootrix/keyword_alert_bot/issues/20

 ### 5. 同时存在多关键字如何匹配

```
/(?=.*cc)(?=.*bb)(?=.*aa).*/
```


## ☕ Buy me a coffee

[USDT-TRC20]：`TDELNhqYjMJvrChjcTBiBBieWYiDGiGm2r`

<p align="center">
  <img height="260" alt="wechat pay" src="https://user-images.githubusercontent.com/10736915/231505942-533e5299-54bd-44e3-aed5-2cff2b893960.jpg" />
  <img height="260" alt="alipay" src="https://user-images.githubusercontent.com/10736915/231506223-47475d4e-3c89-4aef-ae6a-f7561c948503.jpg" />
  <a target="_blank" href="https://paypal.me/hootrix?country.x=US&locale.x=zh_XC"><img height="260" alt="paypal" src="https://user-images.githubusercontent.com/10736915/231512737-299a2074-3ce1-42b7-9230-0e34d715bca1.jpg" /></a>
  
</p>
