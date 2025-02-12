# -*- coding: utf-8 -*-
# Author: Runsheng Xu <rxx3386@ucla.edu>, Hao Xiang <haxiang@g.ucla.edu>
# License: TDG-Attribution-NonCommercial-NoDistrib


import argparse
import os
import time
import pickle

import numpy as np

import torch
from torch.utils.data import DataLoader

import opencood.hypes_yaml.yaml_utils as yaml_utils
from opencood.tools import train_utils, inference_utils
from opencood.data_utils.datasets import build_dataset
from opencood.utils import eval_utils
from opencood.utils.eval_utils2 import Evaluator 
from opencood.visualization import simple_vis
from opencood.data_utils.datasets.intermediate_fusion_dataset_dair import load_json
from tqdm import tqdm

def test_parser():
    parser = argparse.ArgumentParser(description="synthetic data generation")
    parser.add_argument('--model_dir', type=str, required=True,
                        help='Continued training path')
    parser.add_argument('--fusion_method', type=str,
                        default='intermediate',
                        help='no, no_w_uncertainty, late, early or intermediate')
    parser.add_argument('--save_vis_n', type=int, default=10,
                        help='save how many numbers of visualization result?')
    parser.add_argument('--save_npy', action='store_true',
                        help='whether to save prediction and gt result'
                             'in npy file')
    parser.add_argument('--eval_epoch', type=str, default=None,
                        help='Set the checkpoint')
    parser.add_argument('--comm_thre', type=float, default=None,
                        help='Communication confidence threshold')
    opt = parser.parse_args()
    return opt

def baseline_pkl2dict(frame_id):
    baseline_result_path = "/root/result"
    pkl_file = os.path.join(baseline_result_path, "{:06}".format(frame_id) + ".pkl")
    if os.path.exists(pkl_file):
        with open(pkl_file, 'rb') as f:
            results = pickle.load(f)
        boxes_3d, labels_3d, scores_3d = results["boxes_3d"], results["labels_3d"], results["scores_3d"]
        gt_boxes_3d = results["label"]
        gt_labels_3d, gt_scores_3d = 2 * np.ones((gt_boxes_3d.shape[0]), dtype=np.int), np.ones((gt_boxes_3d.shape[0]), dtype=np.float)
    else:
        boxes_3d, labels_3d, scores_3d  = np.ones((0, 8, 3)), np.zeros((0)), np.zeros((0))
        gt_boxes_3d, gt_labels_3d, gt_scores_3d  = np.ones((0, 8, 3)), np.zeros((0)), np.zeros((0))
    pred_dict = {"boxes_3d": boxes_3d, "scores_3d": scores_3d, "labels_3d": labels_3d}
    gt_dict = {"boxes_3d": gt_boxes_3d, "scores_3d": gt_scores_3d, "labels_3d": gt_labels_3d}
    return pred_dict, gt_dict


def result2dict(box3d_tensor, score_tensor):
    perm_pred = [0, 4, 7, 3, 1, 5, 6, 2]
    perm_label = [3, 2, 1, 0, 7, 6, 5, 4]
    boxes_3d, scores_3d, labels_3d = [], [], []
    box3d = box3d_tensor.cpu().numpy()
    score = score_tensor.cpu().numpy() if score_tensor is not None else None
    for i in range(box3d.shape[0]):
        b3d = box3d[i,:,:]
        if score_tensor is not None:
            b3d = b3d[perm_label][perm_pred]
        sc = score[i] if score is not None else 1.0
        boxes_3d.append(b3d[np.newaxis, :, :])
        scores_3d.append(sc)
        labels_3d.append(int(2))

    if len(boxes_3d) > 0:
        boxes_3d = np.concatenate(boxes_3d, axis=0)
        scores_3d = np.array(scores_3d)
        labels_3d = np.array(labels_3d)
    else:
        boxes_3d = np.ones((0, 8, 3))
        labels_3d = np.zeros((0))
        scores_3d = np.zeros((0))
    output_dict = {"boxes_3d": boxes_3d, "scores_3d": scores_3d, "labels_3d": labels_3d}
    return output_dict

def main():
    opt = test_parser()
    assert opt.fusion_method in ['late', 'early', 'intermediate', 'intermediate_with_comm', 'no']

    hypes = yaml_utils.load_yaml(None, opt)

    if opt.comm_thre is not None:
        hypes['model']['args']['fusion_args']['communication']['thre'] = opt.comm_thre

    hypes['validate_dir'] = hypes['test_dir']

    test_inference = False
    if "test.json" in hypes['test_dir']:
        test_inference = True
    # assert "test" in hypes['validate_dir']
    left_hand = True if "OPV2V" in hypes['test_dir'] else False
    print(f"Left hand visualizing: {left_hand}")

    print('Dataset Building')
    opencood_dataset = build_dataset(hypes, visualize=True, train=False)
    data_loader = DataLoader(opencood_dataset,
                             batch_size=1,
                             num_workers=4,
                             collate_fn=opencood_dataset.collate_batch_test,
                             shuffle=False,
                             pin_memory=False,
                             drop_last=False)

    print('Creating Model')
    model = train_utils.create_model(hypes)
    # we assume gpu is necessary
    if torch.cuda.is_available():
        model.cuda()
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    print('Loading Model from checkpoint')
    saved_path = opt.model_dir
    if opt.eval_epoch is not None:
        epoch_id = opt.eval_epoch
        epoch_id, model = train_utils.load_saved_model(saved_path, model, epoch_id)
    else:
        epoch_id, model = train_utils.load_saved_model(saved_path, model)
        
    model.eval()

    # Create the dictionary for evaluation
    result_stat = {0.3: {'tp': [], 'fp': [], 'gt': 0},
                   0.5: {'tp': [], 'fp': [], 'gt': 0},
                   0.7: {'tp': [], 'fp': [], 'gt': 0}}
    evaluator = Evaluator(["car"])

    total_comm_rates = []
    # total_box = []
    for i, batch_data in tqdm(enumerate(data_loader)):
        with torch.no_grad():
            batch_data = train_utils.to_device(batch_data, device)
            frame_id = int(batch_data["ego"]["sample_idx"])
            if opt.fusion_method == 'late':
                pred_box_tensor, pred_score, gt_box_tensor = \
                    inference_utils.inference_late_fusion(batch_data,
                                                          model,
                                                          opencood_dataset)
            elif opt.fusion_method == 'early':
                pred_box_tensor, pred_score, gt_box_tensor = \
                    inference_utils.inference_early_fusion(batch_data,
                                                           model,
                                                           opencood_dataset)
            elif opt.fusion_method == 'intermediate':
                pred_box_tensor, pred_score, gt_box_tensor = \
                    inference_utils.inference_intermediate_fusion(batch_data,
                                                                  model,
                                                                  opencood_dataset)
            elif opt.fusion_method == 'no':
                pred_box_tensor, pred_score, gt_box_tensor = \
                    inference_utils.inference_no_fusion(batch_data,
                                                                  model,
                                                                  opencood_dataset)
            
            elif opt.fusion_method == 'intermediate_with_comm':
                pred_box_tensor, pred_score, gt_box_tensor, comm_rates = \
                    inference_utils.inference_intermediate_fusion_withcomm(batch_data,
                                                                  model,
                                                                  opencood_dataset)
                total_comm_rates.append(comm_rates)
            else:
                raise NotImplementedError('Only early, late and intermediate, no, intermediate_with_comm'
                                          'fusion modes are supported.')
            if pred_box_tensor is None:
                continue
            
            if not test_inference:
                # pred_dict, gt_dict = baseline_pkl2dict(frame_id)
                # pred_dict = result2dict(pred_box_tensor, pred_score)
                # gt_dict = result2dict(pred_box_tensor, None)
                # evaluator.add_frame(pred_dict, gt_dict)
                eval_utils.caluclate_tp_fp(pred_box_tensor,
                                        pred_score,
                                        gt_box_tensor,
                                        result_stat,
                                        0.3)
                eval_utils.caluclate_tp_fp(pred_box_tensor,
                                        pred_score,
                                        gt_box_tensor,
                                        result_stat,
                                        0.5)
                eval_utils.caluclate_tp_fp(pred_box_tensor,
                                        pred_score,
                                        gt_box_tensor,
                                        result_stat,
                                        0.7)
            if opt.save_npy:
                npy_save_path = os.path.join(opt.model_dir, 'npy')
                if not os.path.exists(npy_save_path):
                    os.makedirs(npy_save_path)
                inference_utils.save_prediction_gt(pred_box_tensor,
                                                   pred_score,
                                                   gt_box_tensor,
                                                   batch_data['ego'][
                                                       'origin_lidar'][0],
                                                   frame_id,
                                                   npy_save_path)

            if opt.save_vis_n and opt.save_vis_n >i:
                vis_save_path = os.path.join(opt.model_dir, 'vis_3d')
                if not os.path.exists(vis_save_path):
                    os.makedirs(vis_save_path)
                vis_save_path = os.path.join(opt.model_dir, 'vis_3d/3d_%05d.png' % frame_id)
                simple_vis.visualize(pred_box_tensor,
                                    gt_box_tensor,
                                    batch_data['ego']['origin_lidar'][0],
                                    hypes['postprocess']['gt_range'],
                                    vis_save_path,
                                    method='3d',
                                    left_hand=left_hand,
                                    vis_pred_box=True)
                
                vis_save_path = os.path.join(opt.model_dir, 'vis_bev')
                if not os.path.exists(vis_save_path):
                    os.makedirs(vis_save_path)
                vis_save_path = os.path.join(opt.model_dir, 'vis_bev/bev_%05d.png' % frame_id)
                simple_vis.visualize(pred_box_tensor,
                                    gt_box_tensor,
                                    batch_data['ego']['origin_lidar'][0],
                                    hypes['postprocess']['gt_range'],
                                    vis_save_path,
                                    method='bev',
                                    left_hand=left_hand,
                                    vis_pred_box=True)
    # print('total_box: ', sum(total_box)/len(total_box))

    if len(total_comm_rates) > 0:
        comm_rates = (sum(total_comm_rates)/len(total_comm_rates)).item()
    else:
        comm_rates = 0
    ap_30, ap_50, ap_70 = eval_utils.eval_final_results(result_stat, opt.model_dir)
    
    with open(os.path.join(saved_path, 'result.txt'), 'a+') as f:
        msg = 'Epoch: {} | AP @0.3: {:.04f} | AP @0.5: {:.04f} | AP @0.7: {:.04f} | comm_rate: {:.06f}\n'.format(epoch_id, ap_30, ap_50, ap_70, comm_rates)
        if opt.comm_thre is not None:
            msg = 'Epoch: {} | AP @0.3: {:.04f} | AP @0.5: {:.04f} | AP @0.7: {:.04f} | comm_rate: {:.06f} | comm_thre: {:.04f}\n'.format(epoch_id, ap_30, ap_50, ap_70, comm_rates, opt.comm_thre)
        f.write(msg)
        print(msg)

    # evaluator.print_ap("3d")
    # evaluator.print_ap("bev")

if __name__ == '__main__':
    main()
