CUDA_VISIBLE_DEVICES=0,1 python -m torch.distributed.launch --nproc_per_node=2 opencood/tools/train.py --hypes_yaml opencood/hypes_yaml/dair-v2x/dair_where2comm_max_multiscale_resnet.yaml
 # python opencood/tools/inference.py --model_dir opencood/logs/dair_where2comm_attn_multiscale_resnet_2022_12_06_05_15_36/ --fusion_method intermediate_with_comm

 