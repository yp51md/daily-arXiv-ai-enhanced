#!/bin/bash

# arxiv-api.sh
url=${1:-"https://dw-dengwei.github.io/daily-arXiv-ai-enhanced/?category=cs.CV"}

# 检测node是否安装
if ! command -v node &> /dev/null; then
    echo "错误: Node.js 未安装"
    echo "请先安装 Node.js: https://nodejs.org/"
    exit 1
fi

# 检测puppeteer是否安装
if ! npm list puppeteer &> /dev/null; then
    echo "警告: puppeteer 未安装"
    echo "正在安装 puppeteer..."
    npm install puppeteer
    if [ $? -ne 0 ]; then
        echo "错误: puppeteer 安装失败"
        exit 1
    fi
    echo "puppeteer 安装成功"
fi

node -e "
const puppeteer = require('puppeteer');
(async () => {
  const browser = await puppeteer.launch({headless: 'new'});
  const page = await browser.newPage();
  await page.goto('$url', {waitUntil: 'networkidle0'});
  const content = await page.content();
  console.log(content);
  await browser.close();
})();
"
