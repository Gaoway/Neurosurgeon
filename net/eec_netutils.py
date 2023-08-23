import socket
import time
import pickle
import torch
import platform
import speedtest as spt
from utils import inference_utils


def start_end_client(ip,port0,input_x,model_type,ee_layer_index, ec_layer_index, device):
    """
    启动一个client客户端 向server端发起推理请求
    一般仅在 edge_api.py 中直接调用
    :param ip: server端的ip地址
    :param port: server端的端口地址
    :param model_type: 选用的模型类型
    :param input_x: 初始输入
    :param partition_point 模型划分点
    :param device: 在本地cpu运行还是cuda运行
    :return: None
    """
    conn0 = get_socket_client(ip, port0)

    # 发送模型类型
    send_short_data(conn0, model_type, msg="model type")

    # 读取模型
    model = inference_utils.get_dnn_model(model_type)

    # 发送划分点
    partition_point = [ee_layer_index, ec_layer_index]
    send_short_data(conn0, partition_point, msg="partition strategy")

    end_model, _ = inference_utils.model_partition(model, ee_layer_index)
    end_model = end_model.to(device)

    # 开始边缘端的推理 首先进行预热
    inference_utils.warmUp(end_model, input_x, device)
    end_output,end_latency = inference_utils.recordTime(end_model,input_x,device,epoch_cpu=30,epoch_gpu=100)
    print(f"{model_type} 在终端设备上推理完成 - {end_latency:.3f} ms")
    
    # 连续接收两个消息 防止消息粘包 end-egde
    conn0.recv(40)
    # 发送中间数据 to edge_device
    send_data(conn0,end_output,"end output")

    transfer_latency = get_short_data(conn0)
    print(f"{model_type} 传输完成 - {transfer_latency:.3f} ms")

    # 连续接收两个消息 防止消息粘包
    #conn0.sendall("avoid sticky".encode())

    cloud_latency = get_short_data(conn0)
    print(f"{model_type} 在云端设备上推理完成 - {cloud_latency[0]:.3f}, {cloud_latency[1]:.3f} ms")

    print("================= DNN Collaborative Inference Finished. ===================")
    conn0.close()
    
def start_cloud_server(conn1, device):
    """_进行推理云服务器_
    监听来自边缘服务器的消息
    Args:
        socket_server2 (_type_): _socket服务器_
        device (_type_): _cpu or gpu caculate device_
    """
    start_time = time.time()
    
    # 接收模型类型
    model_type = get_short_data(conn1)
    print(f"get model type: {model_type} successfully.")
    # 读取模型
    model = inference_utils.get_dnn_model(model_type)
    
    # 接收模型分层点
    partition_point = get_short_data(conn1)
    print(f"get partition point: {partition_point} successfully.")   

    _,cloud_model = inference_utils.model_partition(model, partition_point[1])
    cloud_model = cloud_model.to(device)
    
    # 连续发送两个消息 防止消息粘包
    conn1.sendall("avoid sticky".encode())
    
    # 接收中间数据并返回传输时延
    edge_output,transfer_latency = get_data(conn1)
    print(f"get edge_output and transfer latency successfully.")
    send_short_data(conn1,transfer_latency,"transfer latency")
    
    # 连续发送两个消息 防止消息粘包
    #conn1.recv(40)
    
    inference_utils.warmUp(cloud_model, edge_output, device)
    # 记录云端推理时延
    cloud_output,cloud_latency = inference_utils.recordTime(cloud_model, edge_output,device,epoch_cpu=30,epoch_gpu=100)
    send_short_data(conn1, cloud_latency, "cloud latency")

    print("================= DNN Collaborative Inference Finished. ===================")


def start_edge_server(socket_server1, device, conn1):    #edge从end获得数据，并将数据传输给cloud
    """
    开始监听客户端传来的消息
    并将数据传输给cloud
    一般仅在 cloud_api.py 中直接调用
    :param socket_server: socket服务端
    :param device: 使用本地的cpu运行还是cuda运行
    :return: None
    """
    start_time = time.time()

    # 等待客户端连接
    conn0, client = wait_client(socket_server1)

    # 接收模型类型
    model_type = get_short_data(conn0)
    print(f"get model type: {model_type} successfully.")
    
    # 发送模型类型到cloud
    send_short_data(conn1, model_type, msg="model type")

    # 读取模型
    model = inference_utils.get_dnn_model(model_type)

    # 接收模型分层点
    partition_point = get_short_data(conn0)
    print(f"get partition point {partition_point[0]}, {partition_point[1]} successfully.")
    # 发送划分点
    send_short_data(conn1, partition_point, msg="partition strategy")   # Appear bug

    _,edge_model    = inference_utils.model_partition(model, partition_point[0])
    edge_model,_    = inference_utils.model_partition(model, partition_point[1])  
    edge_model = edge_model.to(device)
    
    # 连续发送两个消息 防止消息粘包 end-edge
    conn0.sendall("avoid sticky".encode())
    # 接收中间数据并返回传输时延
    edge_output,transfer_latency = get_data(conn0)
    print(f"get edge_output and transfer latency successfully.")
    send_short_data(conn0,transfer_latency,"transfer latency")

    # 连续发送两个消息 防止消息粘包
    #conn0.recv(40)

    inference_utils.warmUp(edge_model, edge_output, device)
    # 记录边缘服务器端推理时延
    edge_output,edge_latency = inference_utils.recordTime(edge_model, edge_output,device,epoch_cpu=30,epoch_gpu=100)
    #send_short_data(conn0, edge_latency, "edge latency")
    
    # 连续接收两个消息 防止消息粘包 edge-cloud
    conn1.recv(40)
    
    send_data(conn1,edge_output,"edge output")
    transfer_latency = get_short_data(conn1)
    print(f"{model_type} edge中间数据传输完成 - {transfer_latency:.3f} ms")
    
    cloud_latency = get_short_data(conn1)
    print(f"{model_type} 在云端设备上推理完成 - {cloud_latency:.3f} ms")
    
    send_short_data(conn0, [edge_latency,cloud_latency], "edge latency")

    print("================= DNN Collaborative Inference Finished. ===================")
    #conn1.close()


def get_socket_server(ip, port, max_client_num=10):
    """
    为服务端 - 云端设备创建一个socket 用来等待客户端连接
    :param ip: 云端设备机器的ip
    :param port: socket的网络端口
    :param max_client_num: 最大可连接的用户数
    :return: 创建好的socket
    """
    socket_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # 创建socket

    # 判断使用的是什么平台
    sys_platform = platform.platform().lower()
    if "windows" in sys_platform:
        socket_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # windows
    else:
        if not hasattr(socket, 'SO_REUSEPORT'):
            socket.SO_REUSEPORT = 15
        socket_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1) # macos or linux

    socket_server.bind((ip, port))  # 绑定端口号
    socket_server.listen(max_client_num)  # 打开监听
    return socket_server


def get_socket_client(ip, port):
    """
    客户端(边端设备)创建一个socket 用于连接云端设备
    :param ip: 要连接的云端设备机器的ip
    :param port: 云端设备socket的端口
    :return: 创建好的连接
    """
    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn.connect((ip, port))
    return conn


def close_conn(conn):
    """
    边端设备 终止conn连接
    :param conn: conn连接
    :return: 终止连接
    """
    conn.close()


def close_socket(p):
    """
    云端设备 关闭socket
    :param p: socket
    :return:关闭连接
    """
    p.close()


def wait_client(p):
    """
    等待一次conn连接
    :param p: socket
    :return:
    """
    conn, client = p.accept()
    print(f"successfully connection :{conn}")
    return conn,client


def send_data(conn, x, msg="msg", show=True, DBG=True):
    """
    向另一方发送较长数据 例如DNN模型中间层产生的tensor
    注意：接收数据需要使用get_data函数
    这个send_data消息主要分为： 发送数据长度 - 接收回应 - 发送真实数据 - 接收回应
    :param conn: 客户端的conn连接
    :param x: 要发送的数据
    :param msg: 对应的 提示
    :param show: 是否展示数据通信消息
    :return:
    """
    send_x = pickle.dumps(x)
    conn.sendall(pickle.dumps(len(send_x)))
    if DBG:
        print(f'send_data: sent len data: {len(send_x)}')
        
    resp_len = conn.recv(1024).decode()     # get yes len msg
    if DBG:
        print(f'send_data: get sent len data back: {resp_len}')
        
    if resp_len == 'not receive':
        conn.sendall(pickle.dumps(len(send_x)))
        resp_len = conn.recv(1024).decode()
        print(f'send_data2: get sent len data back: {resp_len}')
    
    conn.sendall(send_x)                # send intermediate data
    if DBG:
        print(f'send_data: sent intermediate data: {len(send_x)}')
    resp_data = conn.recv(1024).decode()    # get yes msg
    if show:
        print(f"get {resp_data} , {msg} has been sent successfully")  # 表示对面已收到数据



def send_short_data(conn, x, msg="msg", show=True):
    """ 向另一方发送比较短的数据 接收数据直接使用get_short_data"""
    send_x = pickle.dumps(x)
    conn.sendall(send_x)
    if show:
        print(f"short message , {msg} has been sent successfully")  # 表示对面已收到数据



def get_data(conn, DBG=True):
    """
    获取一次长数据 主要分为 获取数据长度 - 回应 - 获取数据 - 回应
    :param conn: 建立好的连接
    :return: 解析后的数据 和 获取数据消耗的时延
    """
    # 接收数据长度  
    
    conn.settimeout(5)
    try:
        if DBG:
            print(f'get_data: wait for data len')
        data_len = pickle.loads(conn.recv(1024))  # not get info from client when first run inference of vgg model
        if DBG:
            print(f'get_data: get data len: {(data_len)}')
    except socket.timeout:
        conn.sendall("not receive".encode())
        print(f'get_data: send notrsv msg')
        data_len = pickle.loads(conn.recv(1024))
        
    conn.sendall("yes len".encode())

    # 接收数据并记录时延
    sum_time = 0.0
    data = [conn.recv(1)]
    while True:
        start_time = time.perf_counter()
        packet = conn.recv(40960)
        end_time = time.perf_counter()
        transport_time = (end_time - start_time) * 1000  # 单位转换成ms
        sum_time += transport_time

        data.append(packet)
        if len(b"".join(data)) >= data_len:
            break
        # if len(packet) < 4096: break

    parse_data = pickle.loads(b"".join(data))
    conn.sendall("yes".encode())
    return parse_data,sum_time


def get_short_data(conn):
    """ 获取短数据"""
    socket.setdefaulttimeout(120)
    
    data    = None
    while data == None:
        try:
            data    = conn.recv(1024)
        except:
            print('wait for recv short data')

    return pickle.loads(data)


def get_bandwidth():
    """
    获取当前的网络带宽
    :return: 网络带宽 MB/s
    """
    print("正在获取网络带宽，wait...")
    spd = spt.Speedtest(secure=True)
    spd.get_best_server()

    # download = int(spd.download() / 1024 / 1024)
    upload = int(spd.upload() / 1024 / 1024)

    # print(f'当前下载速度为：{str(download)} MB/s')
    print(f'当前上传速度为：{str(upload)} MB/s')
    return upload


def get_speed(network_type,bandwidth):
    """
    根据speed_type获取网络带宽
    :param network_type: 3g lte or wifi
    :param bandwidth 对应的网络速度 3g单位为KB/s lte和wifi单位为MB/s
    :return: 带宽速度 单位：Bpms bytes_per_ms 单位毫秒内可以传输的字节数
    """
    transfer_from_MB_to_B = 1024 * 1024
    transfer_from_KB_to_B = 1024

    if network_type == "3g":
        return bandwidth * transfer_from_KB_to_B / 1000
    elif network_type == "lte" or network_type == "wifi":
        return bandwidth * transfer_from_MB_to_B / 1000
    else:
        raise RuntimeError(f"目前不支持network type - {network_type}")


def create_server(p):
    """
    使用socket 建立一个 server - 循环等待客户端发来请求
    一般仅在测试的时候进行使用
    :param p: socket连接
    :return: None
    """
    while True:
        conn, client = p.accept()  # 接收到客户端的请求
        print(f"connect with client :{conn} successfully ")

        sum_time = 0.0
        # 收发消息
        data = [conn.recv(1)]  # 为了更准确地记录时间，先获取长度为1的消息，之后开启计时
        while True:
            start_time = time.perf_counter()  # 记录开始时间
            packet = conn.recv(1024)
            end_time = time.perf_counter()  # 记录结束时间
            transport_time = (end_time - start_time) * 1000
            sum_time += transport_time  # 传输时间累计到sum_time变量中

            data.append(packet)
            if len(packet) < 1024:  # 长度 < 1024 代表所有数据已经被接受
                break

        parse_data = pickle.loads(b"".join(data))  # 发送和接收数据都使用pickle包，所以这里进行解析pickle
        print(f"get all data come from :{conn} successfully ")

        if torch.is_tensor(parse_data):  # 主要对tensor数据进行数据大小的衡量
            total_num = 1
            for num in parse_data.shape:
                total_num += num
            data_size = total_num * 4
        else:
            data_size = 0.0

        print(f"data size(bytes) : {data_size} \t transfer time : {sum_time:.3} ms")
        print("=====================================")
        conn.send("yes".encode("UTF-8"))  # 接收到所有请求后回复client
        conn.close()


def show_speed(data_size,actual_latency,speed_Bpms):
    """
    用于比较：
    （1）iperf真实带宽 和 预测带宽
    （2）真实传输时延 和 根据公式计算得出的的预测传输时延
    一般只有测试的时候会使用
    :param data_size: 数据大小 - bytes
    :param actual_latency: 实际传输时延
    :param speed_Bpms: iperf获取的真实带宽
    :return: 展示比较 应该是差不多的比较结果
    """
    print(f"actual speed : {speed_Bpms:.3f} B/ms")  # iperf获取的带宽
    print(f"predicted speed : {(data_size/actual_latency):.3f} B/ms")  # 通过数据大小和真实传输时间计算的带宽

    print(f"actual latency for {data_size} bytes : {actual_latency:.3f} ms")  # 实际记录的时延
    print(f"predicted latency for {data_size} bytes : {(data_size / speed_Bpms):.3f} ms")  # 通过iperf带宽预测的时延