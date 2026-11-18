# 闲鱼智能客服

客服管理系统，支持多账号、AI 回复、人工接管和商品管理。

## 运行

```bash
git clone https://github.com/owenfff/xianyu-smart-customer-service.git
cd xianyu-smart-customer-service
docker compose up -d --build
```

访问 `http://localhost:9000`。Windows 也可运行 `start-local.bat`；请勿与 Docker 同时启动。

联网部署前请修改管理员密码和 JWT 密钥，并妥善保管账号数据。

开源协议：[AGPL-3.0](LICENSE)。
