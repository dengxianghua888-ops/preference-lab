# 默认审核手册入口

规则正文的唯一维护位置为 [包内 guidelines.md](src/ecoalign_forge/resources/guidelines.md)。

源码运行、wheel 安装和 sdist 构建均通过 `importlib.resources` 读取这一份正文；
当前工作目录中的同名文件不会覆盖规则。本入口不复制规则内容。

该默认手册覆盖私域引流与低信息量内容领域。内核也支持声明式 PolicyPack；
通过 `run --policy` 为新运行选择规则包，见 [规则包示例](examples/README.md)。
已保存运行的规则快照不可原地切换；自定义规则支持不代表模型质量已经验证。
