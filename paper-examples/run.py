"""Repeat the six VibeEdit case studies using the local blue-chair ComfyUI workflow."""
import argparse
import json
from pathlib import Path
from runpy import run_path

ROOT = Path(__file__).resolve().parent
CHAIR = ROOT.parent / "blue-chair"
ASSETS = ROOT / "assets"
RUN_WORKFLOW = run_path(str(CHAIR / "run.py"))["run_workflow"]
CASES = {
    "rose": "Add one red rose on the wooden table at the circled location.",
    "patio": "Remove only the gray cushion underneath the red scribble on the back-left wooden bench. Keep that bench and its other cushion.",
    "fern": "Replace only the second pair of boots from the left, inside the red circle, with a tall potted fern. Keep the other three pairs of boots.",
    "headphones": "Change only the outer earcup inside the red circle on the third headset from the left to deep teal. Keep its headband and the other headphones unchanged.",
    "cats": "Move the circled cream cat figurine (fourth from the left on the lower shelf) into the green circle at the far right of the upper shelf. Leave its old spot empty and keep every other figurine where it was.",
    "suitcase": "Replace the blue folding chair to the right of the hiker with one red suitcase. Preserve the hiker and mountain scenery.",
}
GENERIC = (
    "Edit this photograph according to the instructions drawn on it. Read the handwritten note. "
    "A circle selects an object or a place; a scribble marks something to remove. "
    "An arrow means move the selected object to its destination and leave its original spot empty. "
    "Return a clean photograph with all instruction text, circles, scribbles and arrows removed. "
    "Preserve all unrelated objects and the rest of the scene."
)
REFINED = {
    "patio": (
        "On the wooden loveseat in the upper left of the picture, remove the LEFT of its two "
        "gray cushions, the one closest to the dining table and directly covered by red scribbles. "
        "Keep the RIGHT cushion of that same loveseat where it is. The left half of the seat should "
        "show bare brown wood. Keep all other cushions and furniture. Remove every red marking and all lettering."
    ),
}


def case_inputs(case, mode):
    if case == "suitcase":
        return ("method-source" if mode == "text" else "method-annotated") + ".webp"
    return f"{case}-canvas.webp"


def execute(case, mode):
    graph = json.loads((CHAIR / "canvas.api.json").read_text())
    input_name = case_inputs(case, mode)
    prompt = {
        "canvas": "", "generic": GENERIC, "guided": CASES[case],
        "text": CASES[case], "refined": REFINED.get(case),
    }[mode]
    if mode == "guided":
        prompt += " Remove all drawn marks and instruction lettering from the final image. Preserve the rest of the scene."
    graph["5"]["inputs"]["prompt"] = prompt
    graph["6"]["inputs"]["image"] = f"INFH5000-paper-examples/{input_name}"
    graph["9"]["inputs"]["filename_prefix"] = f"INFH5000-paper-examples/{case}-{mode}-seed42"
    RUN_WORKFLOW(graph, ASSETS / input_name, ROOT / f"{case}-{mode}.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="*", help="Case names; by default run all cases")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--generic-only", action="store_true", help="Run only the shared canvas-reading prompt")
    group.add_argument("--refined", action="store_true", help="Run the documented location-clarification retries")
    args = parser.parse_args()
    for case in args.cases or (REFINED if args.refined else CASES):
        if case not in CASES:
            parser.error(f"unknown case {case!r}; select from {', '.join(CASES)}")
        if args.refined and case not in REFINED:
            parser.error(f"no refined prompt for {case}")
        modes = ("canvas", "generic", "text" if case == "suitcase" else "guided")
        if args.generic_only:
            modes = ("generic",)
        if args.refined:
            modes = ("refined",)
        for mode in modes:
            execute(case, mode)
