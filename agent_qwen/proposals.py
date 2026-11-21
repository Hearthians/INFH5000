"""A proposal is an ordered list of image edits in full-frame coordinates."""

from .contracts import TEXT, InvalidResponse, checked, choice, items, record


COORD = {"type": "integer", "minimum": 0, "maximum": 1000}
LENGTH = {"type": "integer", "minimum": 1, "maximum": 1000}
BOX = record(x=COORD, y=COORD, width=LENGTH, height=LENGTH)
POINT = record(x=COORD, y=COORD)


def step_type(*ops, **parameters):
    return record(op=choice(*ops), target=TEXT, shape=choice("rect", "ellipse"), region=BOX,
                  **parameters)


STEP = {"anyOf": [
    step_type("move", to=POINT),
    step_type("remove", "protect"),
    step_type("add", "replace", "restyle", description=TEXT),
    step_type("rotate", degrees={"type": "integer", "minimum": -180, "maximum": 180}),
    step_type("resize", percent={"type": "integer", "minimum": 10, "maximum": 300}),
]}
PROPOSAL = record(title=TEXT, rationale=TEXT, steps={**items(STEP), "minItems": 1, "maxItems": 8})
SUGGESTIONS = record(plans={**items(PROPOSAL), "maxItems": 2}, question=TEXT)


def check_proposal(plan):
    checked(PROPOSAL, plan)
    if not plan["title"].strip() or not plan["rationale"].strip():
        raise InvalidResponse("方案缺少名称或推荐理由。")
    for index, step in enumerate(plan["steps"], 1):
        box = step["region"]
        if box["x"] + box["width"] > 1000 or box["y"] + box["height"] > 1000:
            raise InvalidResponse(f"操作 {index} 的选区超出图片边界。")
        if not step["target"].strip() or ("description" in step and not step["description"].strip()):
            raise InvalidResponse(f"请补全操作 {index} 的对象和编辑说明。")
        if step.get("degrees") == 0 or step.get("percent") == 100:
            raise InvalidResponse(f"操作 {index} 的变换幅度没有变化。")
        if step["op"] == "move" and (abs(step["to"]["x"] - box["x"] - box["width"] / 2) < 1
                                     and abs(step["to"]["y"] - box["y"] - box["height"] / 2) < 1):
            raise InvalidResponse(f"操作 {index} 的移动终点与起点重合。")
    if all(step["op"] == "protect" for step in plan["steps"]):
        raise InvalidResponse("方案至少需要一项实际编辑。")
    return plan


def check_suggestions(result):
    checked(SUGGESTIONS, result)
    if bool(result["plans"]) == bool(result["question"].strip()):
        raise InvalidResponse("请给出可用方案，或提出一个具体的澄清问题。")
    for plan in result["plans"]:
        check_proposal(plan)
    return result


def step_instruction(step):
    """Compile current geometry for generation and for the independent acceptance check."""
    box = step["region"]
    subject = (f'{step["target"]} (selection {step["shape"]}, left {box["x"]}, top {box["y"]}, '
               f'width {box["width"]}, height {box["height"]})')
    op = step["op"]
    if op == "move":
        return (f'Move the same {subject} to center ({step["to"]["x"]}, {step["to"]["y"]}). '
                'Vacate the original spot and preserve the number of instances.')
    if op == "rotate":
        return f'Rotate {subject} by {step["degrees"]} degrees clockwise about its center.'
    if op == "resize":
        return f'Resize {subject} to {step["percent"]}% of its original linear size about its center.'
    if op == "protect":
        return f'Preserve the identity, appearance and placement of {subject}.'
    if op == "remove":
        return f'Remove {subject} and reconstruct the exposed background.'
    verb = {"add": "Add", "replace": "Replace", "restyle": "Restyle"}[op]
    return f'{verb} {subject} as described: {step["description"]}'


FRAME = "All selections and destinations use full-image coordinates from 0 to 1000, from top left."
