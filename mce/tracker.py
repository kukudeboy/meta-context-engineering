class TokenLimitExceededError(Exception):
    """自定义异常：当全局 Token 消耗超过预设上限时抛出。"""
    pass

class TokenTracker:
    def __init__(self):
        # 记录目前总共消耗了多少 token
        self.total_tokens = 0
        # 记录设定的最大上限，默认为 None（即无限制）
        self.max_limit = None 

    def set_limit(self, limit: int):
        """设置全局预算限制（例如 1,000,000）"""
        self.max_limit = limit

    def add(self, tokens: int):
        """累加新消耗的 token"""
        self.total_tokens += tokens

    def add_usage(self, usage: dict):
        """累加 Claude SDK usage，并在达到预算后抛出异常。"""
        token_fields = (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        self.add(sum(int(usage.get(field, 0) or 0) for field in token_fields))
        if self.is_exceeded():
            raise TokenLimitExceededError(
                f"Token预算已耗尽！已使用 {self.total_tokens}，"
                f"限制为 {self.max_limit}"
            )

    def is_exceeded(self):
        """检查当前使用量是否大于或等于最大上限"""
        if self.max_limit is None:
            return False
        return self.total_tokens >= self.max_limit

# 实例化一个全局的 tracker 对象（单例模式）
global_tracker = TokenTracker()
