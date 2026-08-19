this part of the project builds on top of the LingBot-Depth technology built by 

```bibtex
@article{lingbot-depth2026,
  title={Masked Depth Modeling for Spatial Perception},
  author={Tan, Bin and Sun, Changjiang and Qin, Xiage and Adai, Hanat and Fu, Zelin and Zhou, Tianxiang and Zhang, Han and Xu, Yinghao and Zhu, Xing and Shen, Yujun and Xue, Nan},
  journal={arXiv preprint arXiv:2601.17895},
  year={2026}
}
```

the lingbot-depth tool is used to refine depth images and produce a much cleaner depth image for better analysis and evaluation of the safety/viability of traversal of a path. 

as of august 7, the UAV depth camera is unclear whether it exist or not, so i am using Depth-Anything to generate a depth image from stock images taken from online. then running it through the pipeline 

main pipeline entry is uv run run_uav_navigation.py -> with the necessary arguments. 

Sample run: 
uv run python run_uav_navigation.py `
   --image "examples/sample_pictures/park.jpg" `
   --scene outdoor `
   --no-mask `
   --pitch-deg -35 --cam-height-m 15 --resolution 0.5 `
   --slope-safe 30 --slope-max 55 `
   --path-out result_path_park `


Outstanding work to be done: 
film my own outdoor video from aerial view, figure out how to break down into frames, since depth-anything is only able to generate depth images for static images, not videos. so break into frames, generate depth images, and compute the path at every instance

1. film outdoor aerial video 
2. break video into frames 
3. run depth anything on each frame 
4. compute path at every instance, and better if can combine these paths of different images
5. 

x. test path planning algorithms (later)

