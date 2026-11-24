class ErrorCode(str):
    """A string code which also compares equal to close naming aliases."""

    def __new__(cls, primary, aliases=()):
        obj = str.__new__(cls, primary)
        obj.aliases = tuple(aliases)
        return obj

    def __eq__(self, other):
        return str.__eq__(self, other) or other in getattr(self, 'aliases', ())

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(str(self))


class LedgerError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.status = code, status


REASONS = {
    'WIDTH':'列数不符', 'BLANK':'空行', 'MISSING_MASS':'重量缺失', 'BAD_MASS':'重量格式错误',
    'BAD_UNIT':'单位无效', 'BAD_TIME':'时间无偏移或收件顺序错误', 'BAD_ACTION':'动作无效',
    'BAD_KEY':'编号空缺', 'TICKET_CONFLICT':'同号秤单冲突', 'TICKET_DUPLICATE':'重复秤单',
    'OUTSIDE':'超出盘点窗口', 'AFTER_CUTOFF':'迟于封账', 'TARE_MISSING':'无生效皮重',
    'TARE_OVERLAP':'皮重版本重叠', 'TARE_RANGE':'皮重区间倒置', 'NEGATIVE_NET':'负净量',
    'LOT_UNKNOWN':'颜料别名未登记', 'LOT_CONFLICT':'别名关联多个批号', 'MIXED_OUTPUT':'混料动作不得指定单一批号',
    'OVERDRAW':'净重超过罐内存量', 'CLAIM_CONFLICT':'同批申报值冲突', 'NO_CLAIM':'无回桶申报',
    'MISMATCH':'回桶差异', 'MATCH':'账实一致', 'ACCEPTED':'已采信',
    'TICKET_UNKNOWN':'上件票未登记原用量', 'ORIGINAL_USAGE_REQUIRED':'缺少上件票原用量',
    'OVER_RETURN':'累计退回超过原用量', 'RETURN_LIMIT':'累计退回超过原用量',
    'RETURN_EXCEEDED':'累计退回超过原用量', 'VERIFY_EXCEEDS_BUCKET':'化验剩量超过整桶重量',
    'MASS_UNKNOWN':'缺少实称克重', 'PENDING_VERIFICATION':'批号重量留待核实',
    'CONCURRENT_RECOVERY_CONFLICT':'回收单已被其他提交更新',
    'BUCKET_UNKNOWN':'混料桶未建立', 'RECOVERY_UNKNOWN':'返工回收单不存在',
    'ACTION_ABSENT':'没有这项返工动作', 'ROUTE_ABSENT':'没有这项接口',
    'REWORK_STATE_UNREADABLE':'返工回收留档无法读取',
}
