"""Reusable definitions, never a second store of authoritative project facts."""
from __future__ import annotations

import ast
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_UP
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ASSET_KINDS = {
    "ontology": "本体中心", "argument_chain": "论证链", "inference_scenario": "推演场景",
    "variable": "变量定义", "task_dag": "任务图", "function": "函数库", "rule": "规则库",
    "invariant": "不变式", "skill": "技能库", "report_structure": "报告结构",
    "report_form": "报告表单", "prompt": "提示词资产", "quality_gate": "质量闸门",
    "reuse_policy": "复用策略", "entity_relation_model": "实体与关系模型",
    "computation_method": "成本与风险计算方法",
}
EXECUTABLE_KINDS = {"function", "skill", "rule", "inference_scenario", "task_dag"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Variable(Strict):
    key: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_.]{0,99}$")
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(default="", max_length=100)
    value_type: Literal["number", "text", "boolean", "date"] = "number"
    instruction: str = Field(default="", max_length=2000)
    required: bool = True


class Chapter(Strict):
    key: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_.]{0,99}$")
    title: str = Field(min_length=1, max_length=300)
    instruction: str = Field(min_length=1, max_length=2000)
    required_inputs: list[str] = Field(default_factory=list, max_length=100)
    citation_required: bool = True


class ReportStructure(Strict):
    chapters: list[Chapter] = Field(min_length=1, max_length=60)
    attachments: list[str] = Field(default_factory=list, max_length=30)
    style: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def unique_chapters(self):
        for field in ("key", "title"):
            values = [getattr(chapter, field) for chapter in self.chapters]
            if len(values) != len(set(values)):
                raise ValueError("章节标识和标题不能重复")
        return self


class ReportForm(Strict):
    fields: list[Variable] = Field(min_length=1, max_length=100)


class OntologyProperty(Strict):
    name: str
    domain: list[str] = Field(default_factory=list)
    range: list[str] = Field(default_factory=list)


class Ontology(Strict):
    classes: list[str] = Field(min_length=1, max_length=100)
    properties: list[OntologyProperty] = Field(default_factory=list, max_length=100)


class Argument(Strict):
    conclusion: str
    premises: list[str] = Field(min_length=1, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    assumptions: list[str] = Field(default_factory=list, max_length=30)


class Scenario(Strict):
    question: str
    required_inputs: list[str] = Field(min_length=1, max_length=100)
    rule_keys: list[str] = Field(default_factory=list, max_length=30)
    expected_output: str


class Task(Strict):
    key: str
    action: str
    depends_on: list[str] = Field(default_factory=list, max_length=100)


class TaskGraph(Strict):
    tasks: list[Task] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def acyclic(self):
        keys = [task.key for task in self.tasks]
        if len(keys) != len(set(keys)):
            raise ValueError("任务标识不能重复")
        pending = {task.key: set(task.depends_on) for task in self.tasks}
        if any(deps - set(keys) for deps in pending.values()):
            raise ValueError("任务引用了不存在的前置任务")
        done: set[str] = set()
        while pending:
            ready = {key for key, deps in pending.items() if deps <= done}
            if not ready:
                raise ValueError("任务依赖存在循环")
            done |= ready
            pending = {key: deps for key, deps in pending.items() if key not in ready}
        return self


class Function(Strict):
    purpose: str
    implementation_ref: str | None = None
    inputs: list[Variable] = Field(default_factory=list, max_length=100)
    output: Variable


class Rule(Strict):
    conditions: list[str] = Field(min_length=1, max_length=30)
    conclusion: str
    datalog: str | None = Field(default=None, max_length=4000)


class Invariant(Strict):
    condition: str
    message: str
    severity: Literal["warning", "error"] = "warning"


class Skill(Strict):
    purpose: str
    steps: list[str] = Field(min_length=1, max_length=30)
    tool_names: list[str] = Field(default_factory=list, max_length=30)


class Prompt(Strict):
    task: str
    instructions: str
    origin_note: Literal["新编译提示词，不是历史原始提示词"]


class QualityGate(Strict):
    stage: Literal["materials", "generation", "finalization"]
    checks: list[str] = Field(min_length=1, max_length=30)


class ReusePolicy(Strict):
    strategy: Literal["reuse", "parameterize", "branch", "rebuild", "reject"]
    conditions: list[str] = Field(min_length=1, max_length=30)


class EntityRelationModel(Strict):
    entity_types: list[str] = Field(min_length=1, max_length=100)
    predicates: list[str] = Field(default_factory=list, max_length=100)
    identity_rules: list[str] = Field(default_factory=list, max_length=30)


def expression_variables(expression: str) -> set[str]:
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("公式语法不合法") from exc
    if len(expression) > 500 or len(list(ast.walk(tree))) > 100:
        raise ValueError("公式过于复杂")
    variables: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = ast.unparse(node)
            if not all(part.isidentifier() and not part.startswith("_") for part in name.split(".")):
                raise ValueError("非法变量名")
            variables.add(name)
        elif isinstance(node, ast.Constant) and type(node.value) in (int, float):
            numeric_value(node.value)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            visit(node.left); visit(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            visit(node.operand)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"min", "max", "abs"} and not node.keywords:
            if (node.func.id == "abs" and len(node.args) != 1) or not 1 <= len(node.args) <= 20:
                raise ValueError("公式函数参数不合法")
            for arg in node.args: visit(arg)
        else:
            raise ValueError("只允许数值、变量、四则运算和 min/max/abs")

    visit(tree.body)
    return variables


def numeric_value(value: Any) -> Decimal:
    if isinstance(value, bool) or len(str(value)) > 50:
        raise ValueError("数值不合法")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("数值不合法") from exc
    if not number.is_finite() or abs(number) > Decimal("1e30"):
        raise ValueError("数值必须有限且在支持范围内")
    return number


class ComputationMethod(Strict):
    inputs: list[Variable] = Field(min_length=1, max_length=30)
    output: Variable
    expression: str = Field(min_length=1, max_length=500, description="单一算术表达式，只用inputs的英文key，例如required-available；不得含等号、中文名、多条赋值或未绑定变量")
    precision: int = Field(default=2, ge=0, le=8)
    applicability: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def bound_expression(self):
        keys = [item.key for item in self.inputs]
        if len(keys) != len(set(keys)) or self.output.key in keys:
            raise ValueError("输入输出标识不能重复")
        used = expression_variables(self.expression)
        if used != set(keys):
            raise ValueError("公式变量必须与输入标识完全一致")
        if any(item.value_type != "number" for item in self.inputs) or self.output.value_type != "number":
            raise ValueError("公式只支持数值变量")
        return self


PAYLOAD_MODELS = {
    "ontology": Ontology, "argument_chain": Argument, "inference_scenario": Scenario,
    "variable": Variable, "task_dag": TaskGraph, "function": Function, "rule": Rule,
    "invariant": Invariant, "skill": Skill, "report_structure": ReportStructure,
    "report_form": ReportForm, "prompt": Prompt, "quality_gate": QualityGate,
    "reuse_policy": ReusePolicy, "entity_relation_model": EntityRelationModel,
    "computation_method": ComputationMethod,
}


class AssetCandidate(Strict):
    kind: str
    key: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_.-]{0,99}$")
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=3000)
    origin: Literal["extracted_explicit", "inferred_from_sources", "compiled"]
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    used_in_sections: list[str] = Field(default_factory=list, max_length=100)
    dependencies: list[str] = Field(default_factory=list, max_length=100)
    scope: str = Field(min_length=1, max_length=2000)
    payload: dict[str, Any]

    @field_validator("kind")
    @classmethod
    def supported_kind(cls, value):
        if value not in ASSET_KINDS:
            raise ValueError("不支持的写作资产类型")
        return value

    @model_validator(mode="after")
    def typed_payload(self):
        self.payload = PAYLOAD_MODELS[self.kind].model_validate(self.payload).model_dump()
        if self.kind == "prompt" and self.origin != "compiled":
            raise ValueError("从正文提炼的提示词必须标记为新编译")
        return self


class AssetExtraction(Strict):
    assets: list[AssetCandidate] = Field(default_factory=list, max_length=64)
    processed_evidence_ids: list[str] = Field(min_length=1, max_length=100)
    unresolved_items: list[str] = Field(default_factory=list, max_length=100)
    continuation_needed: bool = False


def compute_method(method: ComputationMethod, inputs: dict[str, Any]) -> str:
    if set(inputs) != {item.key for item in method.inputs}:
        raise ValueError("计算输入缺失或包含未知字段")
    values = {key: numeric_value(value) for key, value in inputs.items()}

    def run(node: ast.AST) -> Decimal:
        if isinstance(node, (ast.Name, ast.Attribute)): return values[ast.unparse(node)]
        if isinstance(node, ast.Constant): return numeric_value(node.value)
        if isinstance(node, ast.UnaryOp):
            value = run(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.Call):
            args = [run(arg) for arg in node.args]
            if node.func.id == "abs": return abs(args[0])
            return min(args) if node.func.id == "min" else max(args)
        if isinstance(node, ast.BinOp):
            left, right = run(node.left), run(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if not right: raise ValueError("公式分母不能为零")
            return left / right
        raise ValueError("非法公式节点")

    expression_variables(method.expression)  # validate again at the execution boundary
    with localcontext() as context:
        context.prec = 70
        result = numeric_value(run(ast.parse(method.expression, mode="eval").body))
        return str(result.quantize(Decimal(1).scaleb(-method.precision), rounding=ROUND_HALF_UP))
