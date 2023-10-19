"""
Basic test for the most important function: "analyze_and_unwrap"
"""
import cProfile
import pstats
from pathlib import Path
from typing import Optional
import maya.cmds as cmds
from ZR4M.ZR4M import analyze_and_unwrap as ZR4M_analyze_and_unwrap

def test_analyze_and_unwrap():
    """Check if analyze_and_unwrap() is working with some basic shapes"""

    list_of_geo_to_debug = ["Cube","Cylinder","Cone"]
    for geo in list_of_geo_to_debug:
        if geo == "Cube":
            input_geo = cmds.polyCube(ch=0)[0]
            cmds.polyAutoProjection(input_geo,ch=0)
            cmds.polyMultiLayoutUV(input_geo, sc=1, ps=3, l=2)
            cmds.delete(input_geo,ch=1)
        elif geo == "Cylinder":
            input_geo = cmds.polyCylinder(ch=0)[0]
        elif geo == "Cone":
            input_geo = cmds.polyCone(ch=0)[0]

        list_output_mesh = ZR4M_analyze_and_unwrap(input_geo)
        unwrapped_mesh = list_output_mesh[0]
        list_of_unwrapped_shells = list_output_mesh[1]

        assert cmds.polyEvaluate(input_geo,uv=True) == cmds.polyEvaluate(
            unwrapped_mesh,uv=True), "UV should remain constant"
        assert cmds.polyEvaluate(input_geo,uvShell=True) == len(
            list_of_unwrapped_shells), "UV shells and unwrapped shells should be equal"
        cmds.file(force=True, new=True)

    cmds.inViewMessage(message="Test passed", pos="midCenter",fade=True)

def test_speed_analyze_and_unwrap(output_path: Optional[Path]):
    """Diagnose code by running analyze_and_unwrap() on a dense cube

    Args:
        output_path (Optional[Path]): the path to dump the stats to
    """
    input_geo = cmds.polyCube(sx=100,sy=100,sz=100,ch=0)[0]
    cmds.polyAutoProjection(input_geo,ch=0)
    cmds.polyMultiLayoutUV(input_geo, sc=1, ps=3, l=2)
    cmds.delete(input_geo,ch=1)

    with cProfile.Profile() as profiler:
        ZR4M_analyze_and_unwrap(input_geo)
    stats = pstats.Stats(profiler)
    stats = stats.sort_stats(pstats.SortKey.TIME)
    stats.print_stats()

    if isinstance(output_path, Path):
        stats.dump_stats(filename=Path(tmp_dir / "profiling.prof"))
        print("Dumped to", output_path)
    cmds.file(force=True, new=True)

if __name__ == "__main__":

    master_dir = Path(cmds.internalVar(userScriptDir=True)) / "ZR4M"
    tmp_dir = master_dir / "TMP"
    if master_dir.is_dir() is False or tmp_dir.is_dir() is False:
        tmp_dir.mkdir(parents=True)

    test_analyze_and_unwrap()
    test_speed_analyze_and_unwrap(tmp_dir / "profiling.prof") # use snakeviz or similar
