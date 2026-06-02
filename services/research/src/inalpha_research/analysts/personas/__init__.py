"""投资大师人格 analyst 库（ADR-0037 §A · P1）。

借鉴 FinceptTerminal 的"投资大师 agent"思路，但走 Inalpha 的纪律：复用现有
``Analyst`` Protocol + 辩论 / manager 链路，每个 persona 产出结构化 ``AnalystBrief``。

**可选启用**：persona 不进 ``ALL_ANALYSTS``（核心 6 analyst 永远跑），而是放这个
独立注册表 ``PERSONA_ANALYSTS``；runner 仅在 ``DeepDiveRequest.personas`` 显式指定
时才追加对应 persona。这样普通 deep_dive 成本不变，"大师团辩论"按需启用。

精选 6 个**低相关**人格（两两有天然辩论张力）：

- ``buffett``       价值 / 护城河 / 安全边际        ⟷ wood
- ``lynch``         GARP 成长 / 可理解的生意         ⟷ burry
- ``wood``          颠覆式创新 / 主题高成长          ⟷ buffett
- ``burry``         逆向 / 泡沫警觉 / 深度价值        ⟷ wood
- ``druckenmiller`` 宏观趋势 / 流动性 / 集中下注      ⟷ marks
- ``marks``         周期定位 / 二阶思维 / 风险调整    ⟷ druckenmiller

新增 persona：建一个 ``PersonaAnalyst`` 子类（``type_id`` + ``search_focus`` +
``system_prompt``），加进 ``PERSONA_ANALYSTS``，并在 ``schemas.AnalystBrief.analyst``
Literal 里加对应 ``persona_<key>`` 值（runner 的合法类型集已从该 Literal 动态派生）。
"""
from ..base import Analyst
from .base_persona import PersonaAnalyst
from .buffett import BuffettPersona
from .burry import BurryPersona
from .druckenmiller import DruckenmillerPersona
from .lynch import LynchPersona
from .marks import MarksPersona
from .wood import WoodPersona

#: persona key → analyst 类。key 是对外（请求 / tool）用的短名，类的 ``type_id``
#: 是 ``persona_<key>``（落进 ``AnalystBrief.analyst``）。
PERSONA_ANALYSTS: dict[str, type[Analyst]] = {
    "buffett": BuffettPersona,
    "lynch": LynchPersona,
    "wood": WoodPersona,
    "burry": BurryPersona,
    "druckenmiller": DruckenmillerPersona,
    "marks": MarksPersona,
}

__all__ = [
    "PERSONA_ANALYSTS",
    "BuffettPersona",
    "BurryPersona",
    "DruckenmillerPersona",
    "LynchPersona",
    "MarksPersona",
    "PersonaAnalyst",
    "WoodPersona",
]
