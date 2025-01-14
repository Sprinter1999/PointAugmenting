import numpy as np
import pickle

from pathlib import Path
from functools import reduce
from typing import List

from tqdm import tqdm
from pyquaternion import Quaternion

try:
    from nuscenes import NuScenes
    from nuscenes.utils import splits
    from nuscenes.utils.data_classes import LidarPointCloud
    from nuscenes.utils.geometry_utils import transform_matrix, BoxVisibility
    from nuscenes.utils.data_classes import Box
    from nuscenes.eval.detection.config import config_factory
    from nuscenes.eval.detection.evaluate import NuScenesEval
except:
    print("nuScenes devkit not Found!")

general_to_detection = {
    "human.pedestrian.adult": "pedestrian",
    "human.pedestrian.child": "pedestrian",
    "human.pedestrian.wheelchair": "ignore",
    "human.pedestrian.stroller": "ignore",
    "human.pedestrian.personal_mobility": "ignore",
    "human.pedestrian.police_officer": "pedestrian",
    "human.pedestrian.construction_worker": "pedestrian",
    "animal": "ignore",
    "vehicle.car": "car",
    "vehicle.motorcycle": "motorcycle",
    "vehicle.bicycle": "bicycle",
    "vehicle.bus.bendy": "bus",
    "vehicle.bus.rigid": "bus",
    "vehicle.truck": "truck",
    "vehicle.construction": "construction_vehicle",
    "vehicle.emergency.ambulance": "ignore",
    "vehicle.emergency.police": "ignore",
    "vehicle.trailer": "trailer",
    "movable_object.barrier": "barrier",
    "movable_object.trafficcone": "traffic_cone",
    "movable_object.pushable_pullable": "ignore",
    "movable_object.debris": "ignore",
    "static_object.bicycle_rack": "ignore",
}

cls_attr_dist = {
    "barrier": {
        "cycle.with_rider": 0,
        "cycle.without_rider": 0,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 0,
        "vehicle.parked": 0,
        "vehicle.stopped": 0,
    },
    "bicycle": {
        "cycle.with_rider": 2791,
        "cycle.without_rider": 8946,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 0,
        "vehicle.parked": 0,
        "vehicle.stopped": 0,
    },
    "bus": {
        "cycle.with_rider": 0,
        "cycle.without_rider": 0,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 9092,
        "vehicle.parked": 3294,
        "vehicle.stopped": 3881,
    },
    "car": {
        "cycle.with_rider": 0,
        "cycle.without_rider": 0,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 114304,
        "vehicle.parked": 330133,
        "vehicle.stopped": 46898,
    },
    "construction_vehicle": {
        "cycle.with_rider": 0,
        "cycle.without_rider": 0,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 882,
        "vehicle.parked": 11549,
        "vehicle.stopped": 2102,
    },
    "ignore": {
        "cycle.with_rider": 307,
        "cycle.without_rider": 73,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 165,
        "vehicle.parked": 400,
        "vehicle.stopped": 102,
    },
    "motorcycle": {
        "cycle.with_rider": 4233,
        "cycle.without_rider": 8326,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 0,
        "vehicle.parked": 0,
        "vehicle.stopped": 0,
    },
    "pedestrian": {
        "cycle.with_rider": 0,
        "cycle.without_rider": 0,
        "pedestrian.moving": 157444,
        "pedestrian.sitting_lying_down": 13939,
        "pedestrian.standing": 46530,
        "vehicle.moving": 0,
        "vehicle.parked": 0,
        "vehicle.stopped": 0,
    },
    "traffic_cone": {
        "cycle.with_rider": 0,
        "cycle.without_rider": 0,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 0,
        "vehicle.parked": 0,
        "vehicle.stopped": 0,
    },
    "trailer": {
        "cycle.with_rider": 0,
        "cycle.without_rider": 0,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 3421,
        "vehicle.parked": 19224,
        "vehicle.stopped": 1895,
    },
    "truck": {
        "cycle.with_rider": 0,
        "cycle.without_rider": 0,
        "pedestrian.moving": 0,
        "pedestrian.sitting_lying_down": 0,
        "pedestrian.standing": 0,
        "vehicle.moving": 21339,
        "vehicle.parked": 55626,
        "vehicle.stopped": 11097,
    },
}

def _second_det_to_nusc_box(detection):
    box3d = detection["box3d_lidar"].detach().cpu().numpy()
    scores = detection["scores"].detach().cpu().numpy()
    labels = detection["label_preds"].detach().cpu().numpy()
    box3d[:, -1] = -box3d[:, -1] - np.pi / 2
    box_list = []
    for i in range(box3d.shape[0]):
        quat = Quaternion(axis=[0, 0, 1], radians=box3d[i, -1])
        velocity = (*box3d[i, 6:8], 0.0)
        box = Box(
            box3d[i, :3],
            box3d[i, 3:6],
            quat,
            label=labels[i],
            score=scores[i],
            velocity=velocity,
        )
        box_list.append(box)
    return box_list


def _lidar_nusc_box_to_global(nusc, boxes, sample_token):
    try:
        s_record = nusc.get("sample", sample_token)
        sample_data_token = s_record["data"]["LIDAR_TOP"]
    except:
        sample_data_token = sample_token

    sd_record = nusc.get("sample_data", sample_data_token)
    cs_record = nusc.get("calibrated_sensor", sd_record["calibrated_sensor_token"])
    pose_record = nusc.get("ego_pose", sd_record["ego_pose_token"])

    box_list = []
    for box in boxes:
        # Move box to ego vehicle coord system
        box.rotate(Quaternion(cs_record["rotation"]))
        box.translate(np.array(cs_record["translation"]))
        # Move box to global coord system
        box.rotate(Quaternion(pose_record["rotation"]))
        box.translate(np.array(pose_record["translation"]))
        box_list.append(box)
    return box_list


def _get_available_scenes(nusc):
    available_scenes = []
    print("total scene num:", len(nusc.scene))
    for scene in nusc.scene:
        scene_token = scene["token"]
        scene_rec = nusc.get("scene", scene_token)
        sample_rec = nusc.get("sample", scene_rec["first_sample_token"])
        sd_rec = nusc.get("sample_data", sample_rec["data"]["LIDAR_TOP"])
        has_more_frames = True
        scene_not_exist = False
        while has_more_frames:
            lidar_path, boxes, _ = nusc.get_sample_data(sd_rec["token"])
            if not Path(lidar_path).exists():
                scene_not_exist = True
                break
            else:
                break
        if scene_not_exist:
            continue
        available_scenes.append(scene)
    print("exist scene num:", len(available_scenes))
    return available_scenes


def get_sample_data(
    nusc, sample_data_token: str, selected_anntokens: List[str] = None
):
    """
    Returns the data path as well as all annotations related to that sample_data.
    Note that the boxes are transformed into the current sensor's coordinate frame.
    :param sample_data_token: Sample_data token.
    :param selected_anntokens: If provided only return the selected annotation.
    :return: (data_path, boxes, camera_intrinsic <np.array: 3, 3>)
    """

    # Retrieve sensor & pose records
    sd_record = nusc.get("sample_data", sample_data_token)
    cs_record = nusc.get("calibrated_sensor", sd_record["calibrated_sensor_token"])
    sensor_record = nusc.get("sensor", cs_record["sensor_token"])
    pose_record = nusc.get("ego_pose", sd_record["ego_pose_token"])

    data_path = nusc.get_sample_data_path(sample_data_token)

    if sensor_record["modality"] == "camera":
        cam_intrinsic = np.array(cs_record["camera_intrinsic"])
    else:
        cam_intrinsic = None

    # Retrieve all sample annotations and map to sensor coordinate system.
    if selected_anntokens is not None:
        boxes = list(map(nusc.get_box, selected_anntokens))
    else:
        boxes = nusc.get_boxes(sample_data_token)

    # Make list of Box objects including coord system transforms.
    box_list = []
    for box in boxes:
        box.velocity = nusc.box_velocity(box.token)
        # Move box to ego vehicle coord system
        box.translate(-np.array(pose_record["translation"]))
        box.rotate(Quaternion(pose_record["rotation"]).inverse)

        #  Move box to sensor coord system
        box.translate(-np.array(cs_record["translation"]))
        box.rotate(Quaternion(cs_record["rotation"]).inverse)

        box_list.append(box)

    return data_path, box_list, cam_intrinsic



def _fill_trainval_infos(nusc, train_scenes, val_scenes, test=False, nsweeps=10, filter_zero=True):
    from nuscenes.utils.geometry_utils import transform_matrix

    train_nusc_infos = []
    val_nusc_infos = []

    ref_chan = "LIDAR_TOP"  # The radar channel from which we track back n sweeps to aggregate the point cloud.
    chan = "LIDAR_TOP"  # The reference channel of the current sample_rec that the point clouds are mapped to.
    cam_chan = ['CAM_FRONT', 'CAM_FRONT_RIGHT', 'CAM_BACK_RIGHT', 'CAM_BACK', 'CAM_BACK_LEFT', 'CAM_FRONT_LEFT']
    CAM_SENSOR_DICT = {cam: i for i, cam in enumerate(cam_chan)}
    #TODO: 填每一个样本
    for sample in tqdm(nusc.sample):
        if not (sample["scene_token"] in train_scenes or sample["scene_token"] in val_scenes):
            continue

        """ Manual save info["sweeps"] """
        # Get reference pose and timestamp
        # ref_chan == "LIDAR_TOP"
        ref_sd_token = sample["data"][ref_chan]
        ref_sd_rec = nusc.get("sample_data", ref_sd_token)
        ref_cs_rec = nusc.get(
            "calibrated_sensor", ref_sd_rec["calibrated_sensor_token"]
        )
        ref_pose_rec = nusc.get("ego_pose", ref_sd_rec["ego_pose_token"])
        ref_time = 1e-6 * ref_sd_rec["timestamp"]

        ref_lidar_path, ref_boxes, _ = get_sample_data(nusc, ref_sd_token)

        ref_cam_tokens = {}
        for cam_sensor in cam_chan:
            ref_cam_tokens[cam_sensor] = sample["data"][cam_sensor]

        # Homogeneous transform from ego car frame to reference frame
        ref_from_car = transform_matrix(
            ref_cs_rec["translation"], Quaternion(ref_cs_rec["rotation"]), inverse=True
        )
        ref_to_car = transform_matrix(
            ref_cs_rec["translation"], Quaternion(ref_cs_rec["rotation"]), inverse=False
        )

        # Homogeneous transformation matrix from global to _current_ ego car frame
        car_from_global = transform_matrix(
            ref_pose_rec["translation"],
            Quaternion(ref_pose_rec["rotation"]),
            inverse=True,
        )
        car_to_global = transform_matrix(
            ref_pose_rec["translation"],
            Quaternion(ref_pose_rec["rotation"]),
            inverse=False,
        )
        ref_to_global = reduce(np.dot, [car_to_global, ref_to_car])

        ref_cam_paths = {}
        ref_cam_intrinsics = {}
        cams_from_global = {}
        from collections import defaultdict
        img_boxes_dict = defaultdict(list)
        for cam_sensor in cam_chan:
            cam_token = ref_cam_tokens[cam_sensor]
            ref_cam = nusc.get("sample_data", cam_token)
            ref_cam_cs = nusc.get(
                "calibrated_sensor", ref_cam["calibrated_sensor_token"]
            )
            ref_cam_pose = nusc.get("ego_pose", ref_cam["ego_pose_token"])

            ref_cam_path, img_boxes, ref_cam_intrinsic = nusc.get_sample_data(cam_token,
                                                                              box_vis_level=BoxVisibility.ANY)
            # car to cam transform
            cam_from_car = transform_matrix(
                ref_cam_cs["translation"], Quaternion(ref_cam_cs["rotation"]), inverse=True
            )
            cam_car_from_global = transform_matrix(
                ref_cam_pose["translation"],
                Quaternion(ref_cam_pose["rotation"]),
                inverse=True,
            )

            cam_from_global = reduce(
                np.dot,
                [cam_from_car, cam_car_from_global]
            )

            ref_cam_paths[cam_sensor] = ref_cam_path
            ref_cam_intrinsics[cam_sensor] = ref_cam_intrinsic
            cams_from_global[cam_sensor] = cam_from_global

            if not test:
                from det3d.utils.utils_kitti import KittiDB
                import copy
                for img_box in img_boxes:
                    img_box.translate(np.array([0, img_box.wlh[2] / 2, 0]))
                    bbox, imcorners = KittiDB.project_kitti_box_to_image(
                        copy.deepcopy(img_box), ref_cam_intrinsic, imsize=(1600, 900))
                    bbox = np.array([bbox[0], bbox[1], bbox[2], bbox[3]])
                    imcorners = np.array(imcorners)
                    if img_box.token not in img_boxes_dict:
                        img_boxes_dict[img_box.token] = [{'bbox': bbox, "imcorners": imcorners, 'cam_sensor': cam_sensor, "depth": img_box.center[2]}]
                    else:
                        img_boxes_dict[img_box.token].append({'bbox': bbox, "imcorners": imcorners, 'cam_sensor': cam_sensor, "depth": img_box.center[2]})

        info = {
            "lidar_path": ref_lidar_path,
            "cam_paths": ref_cam_paths,
            "cam_intrinsics": ref_cam_intrinsics,
            "cams_from_global": cams_from_global,
            "token": sample["token"],
            "sweeps": [],
            "ref_from_car": ref_from_car,
            "car_from_global": car_from_global,
            "timestamp": ref_time,
            "ref_to_global": ref_to_global,
        }

        sample_data_token = sample["data"][chan]
        curr_sd_rec = nusc.get("sample_data", sample_data_token)
        sweeps = []
        while len(sweeps) < nsweeps - 1:
            if curr_sd_rec["prev"] == "":
                if len(sweeps) == 0:
                    sweep = {
                        "lidar_path": ref_lidar_path,
                        "sample_data_token": curr_sd_rec["token"],
                        "transform_matrix": None,
                        "time_lag": curr_sd_rec["timestamp"] * 0,
                        # time_lag: 0,
                    }
                    sweeps.append(sweep)
                else:
                    sweeps.append(sweeps[-1])
            else:
                curr_sd_rec = nusc.get("sample_data", curr_sd_rec["prev"])

                # Get past pose
                current_pose_rec = nusc.get("ego_pose", curr_sd_rec["ego_pose_token"])
                global_from_car = transform_matrix(
                    current_pose_rec["translation"],
                    Quaternion(current_pose_rec["rotation"]),
                    inverse=False,
                )

                # Homogeneous transformation matrix from sensor coordinate frame to ego car frame.
                current_cs_rec = nusc.get(
                    "calibrated_sensor", curr_sd_rec["calibrated_sensor_token"]
                )
                car_from_current = transform_matrix(
                    current_cs_rec["translation"],
                    Quaternion(current_cs_rec["rotation"]),
                    inverse=False,
                )

                tm = reduce(
                    np.dot,
                    [ref_from_car, car_from_global, global_from_car, car_from_current],
                )

                lidar_path = nusc.get_sample_data_path(curr_sd_rec["token"])

                time_lag = ref_time - 1e-6 * curr_sd_rec["timestamp"]

                sweep = {
                    "lidar_path": lidar_path,
                    "sample_data_token": curr_sd_rec["token"],
                    "transform_matrix": tm,
                    "global_from_car": global_from_car,
                    "car_from_current": car_from_current,
                    "time_lag": time_lag,
                }
                sweeps.append(sweep)

        info["sweeps"] = sweeps

        assert (
            len(info["sweeps"]) == nsweeps - 1
        ), f"sweep {curr_sd_rec['token']} only has {len(info['sweeps'])} sweeps, you should duplicate to sweep num {nsweeps-1}"
        """ read from api """

        if not test:
            annotations = [
                nusc.get("sample_annotation", token) for token in sample["anns"]
            ]

            mask = np.array([(anno['num_lidar_pts'] + anno['num_radar_pts'])>0 for anno in annotations], dtype=bool).reshape(-1)




            locs = np.array([b.center for b in ref_boxes]).reshape(-1, 3)
            dims = np.array([b.wlh for b in ref_boxes]).reshape(-1, 3)
            # rots = np.array([b.orientation.yaw_pitch_roll[0] for b in ref_boxes]).reshape(-1, 1)
            velocity = np.array([b.velocity for b in ref_boxes]).reshape(-1, 3)
            rots = np.array([quaternion_yaw(b.orientation) for b in ref_boxes]).reshape(
                -1, 1
            )


            names = np.array([b.name for b in ref_boxes])
            tokens = np.array([b.token for b in ref_boxes])
            gt_boxes = np.concatenate([locs, dims, velocity[:, :2], -rots - np.pi / 2], axis=1)
            # gt_boxes = np.concatenate([locs, dims, rots], axis=1)

            assert len(annotations) == len(gt_boxes) == len(velocity)

            #FIXME: 在这里获取frustum
            # get box frustum in lidar view
            from det3d.core.bbox import box_np_ops
            from det3d.datasets.utils.cross_modal_augmentation import transform2Spherical
            num_box = gt_boxes.shape[0]
            gt_box_corners = box_np_ops.center_to_corner_box3d(
                gt_boxes[:, :3], gt_boxes[:, 3:6], gt_boxes[:, -1],).reshape(-1, 3)  # N * 8 * 3 - (N*8)*3
            
            #TODO: 输入的三维笛卡尔坐标系下的点集转换为球坐标系下的表示
            pts_rr = transform2Spherical(gt_box_corners)
            pts_rr = pts_rr.reshape(num_box, 8, 3)

            gt_frustum = np.ones([num_box, 3, 2, 2], dtype=np.float32) * -1  # N * (r, phi, theta) * (min, max) * 2
            gt_frustum[:, :, :, 0] = np.stack([pts_rr.min(axis=1), pts_rr.max(axis=1)], axis=2)
            val = (gt_frustum[:, 2, 1, 0] - gt_frustum[:, 2, 0, 0]) > np.pi
            if val.any():
                idxs = np.where(val > 0)[0]
                gt_frustum[val, 2, 0, 0] = 0.
                gt_frustum[val, 2, 1, 1] = np.pi * 2
                for idx in idxs:
                    gt_frustum[idx, 2, 1, 0] = pts_rr[idx, pts_rr[idx, :, 2] < np.pi, 2].max()
                    gt_frustum[idx, 2, 0, 1] = pts_rr[idx, pts_rr[idx, :, 2] > np.pi, 2].min()

            # get 2d box info
            avail_2d = np.zeros([tokens.shape[0], 6], dtype=np.bool_)
            boxes_2d = np.ones([tokens.shape[0], 6, 4]).astype(np.float32) * -1
            depths = np.zeros([tokens.shape[0], 6]).astype(np.float32)

            for ids, b in enumerate(ref_boxes):
                if b.token in img_boxes_dict:
                    for img_id, cur_box in enumerate(img_boxes_dict[b.token]):
                        # special case, box width or height < 1
                        if (cur_box['bbox'][2] - cur_box['bbox'][0]) < 1. or (
                                cur_box['bbox'][3] - cur_box['bbox'][1]) < 1.:
                            print('invalid box: height or width < 1')
                            continue
                        cam_id = CAM_SENSOR_DICT[cur_box['cam_sensor']]
                        avail_2d[ids, cam_id] = True
                        boxes_2d[ids, cam_id] = cur_box['bbox']
                        depths[ids, cam_id] = cur_box['depth']

            if not filter_zero:
                info["gt_boxes"] = gt_boxes
                info["gt_boxes_velocity"] = velocity
                info["gt_names"] = np.array([general_to_detection[name] for name in names])
                info["gt_boxes_token"] = tokens
                info["gt_frustum"] = gt_frustum
                info["avail_2d"] = avail_2d
                info["boxes_2d"] = boxes_2d
                info["depths"] = depths
            else:
                info["gt_boxes"] = gt_boxes[mask, :]
                info["gt_boxes_velocity"] = velocity[mask, :]
                info["gt_names"] = np.array([general_to_detection[name] for name in names])[mask]
                info["gt_boxes_token"] = tokens[mask]
                info["gt_frustum"] = gt_frustum[mask]
                info["avail_2d"] = avail_2d[mask]
                info["boxes_2d"] = boxes_2d[mask]
                info["depths"] = depths[mask]

        if sample["scene_token"] in train_scenes:
            train_nusc_infos.append(info)
        elif sample["scene_token"] in val_scenes:
            val_nusc_infos.append(info)

    return train_nusc_infos, val_nusc_infos


def quaternion_yaw(q: Quaternion) -> float:
    """
    Calculate the yaw angle from a quaternion.
    Note that this only works for a quaternion that represents a box in lidar or global coordinate frame.
    It does not work for a box in the camera frame.
    :param q: Quaternion of interest.
    :return: Yaw angle in radians.
    """

    # Project into xy plane.
    v = np.dot(q.rotation_matrix, np.array([1, 0, 0]))

    # Measure yaw using arctan.
    yaw = np.arctan2(v[1], v[0])

    return yaw


# 在create info的时候，就把frustum填充进去了
def create_nuscenes_infos(root_path, version="v1.0-trainval", nsweeps=10, rate=1., filter_zero=True):
    nusc = NuScenes(version=version, dataroot=root_path, verbose=True)
    available_vers = ["v1.0-trainval", "v1.0-test", "v1.0-mini"]
    assert version in available_vers
    if version == "v1.0-trainval":
        train_scenes = splits.train
        # random.shuffle(train_scenes)
        # train_scenes = train_scenes[:int(len(train_scenes)*0.2)]
        val_scenes = splits.val
    elif version == "v1.0-test":
        train_scenes = splits.test
        val_scenes = []
    elif version == "v1.0-mini":
        train_scenes = splits.mini_train
        val_scenes = splits.mini_val
    else:
        raise ValueError("unknown")
    test = "test" in version
    root_path = Path(root_path)
    # filter exist scenes. you may only download part of dataset.
    available_scenes = _get_available_scenes(nusc)
    available_scene_names = [s["name"] for s in available_scenes]
    train_scenes = list(filter(lambda x: x in available_scene_names, train_scenes))
    val_scenes = list(filter(lambda x: x in available_scene_names, val_scenes))
    # train_scenes = set(
    #     [
    #         available_scenes[available_scene_names.index(s)]["token"]
    #         for s in train_scenes
    #     ]
    # )
    train_scenes = [
        available_scenes[available_scene_names.index(s)]["token"]
        for s in train_scenes
    ]
    select_num = int(len(train_scenes) * rate)
    print('select train num / all_num:', select_num, len(train_scenes))
    import random
    random.seed(10)
    train_scenes = random.sample(train_scenes, select_num)
    train_scenes = set(train_scenes)
    # print('train_scenes', train_scenes)

    val_scenes = set(
        [available_scenes[available_scene_names.index(s)]["token"] for s in val_scenes]
    )
    if test:
        print(f"test scene: {len(train_scenes)}")
    else:
        print(f"train scene: {len(train_scenes)}, val scene: {len(val_scenes)}")


    #TODO: 在这里填充好了frustum信息
    # [list]
    train_nusc_infos, val_nusc_infos = _fill_trainval_infos(
        nusc, train_scenes, val_scenes, test, nsweeps=nsweeps, filter_zero=filter_zero
    )

    if test:
        print(f"test sample: {len(train_nusc_infos)}")
        with open(
            root_path / "infos_test_{:02d}sweeps_withvelo_crossmodal.pkl".format(nsweeps), "wb"
        ) as f:
            pickle.dump(train_nusc_infos, f)
    else:
        print(
            f"train sample: {len(train_nusc_infos)}, val sample: {len(val_nusc_infos)}"
        )
        with open(
            root_path / "infos_train_{:02d}sweeps_withvelo_filter_{}_{:03d}rate_crossmodal.pkl".format(nsweeps, filter_zero, int(rate*100)), "wb"
        ) as f:
            pickle.dump(train_nusc_infos, f)
        with open(
            root_path / "infos_val_{:02d}sweeps_withvelo_filter_{}_crossmodal.pkl".format(nsweeps, filter_zero), "wb"
        ) as f:
            pickle.dump(val_nusc_infos, f)


def eval_main(nusc, eval_version, res_path, eval_set, output_dir):
    # nusc = NuScenes(version=version, dataroot=str(root_path), verbose=True)
    cfg = config_factory(eval_version)

    nusc_eval = NuScenesEval(
        nusc,
        config=cfg,
        result_path=res_path,
        eval_set=eval_set,
        output_dir=output_dir,
        verbose=True,
    )
    metrics_summary = nusc_eval.main(plot_examples=10,)
