# -*- coding: utf-8 -*-
import sys
from pyqtgraph.Qt import QtWidgets, QtCore  # 导入QtCore
import threading
import pyqtgraph as pg
import serial
import time

# 全局数据存储
data = []
data2 = []
data3 = []
stop_event = threading.Event()
from pylsl import StreamInfo, StreamOutlet

# 全局变量中添加LSL Outlet
info_eeg = StreamInfo(name='EEG_Raw', type='EEG', channel_count=1, nominal_srate=256, channel_format='int16', source_id='TGAM_EEG')
outlet_eeg = StreamOutlet(info_eeg)

info_attention = StreamInfo(name='Attention', type='Markers', channel_count=1, nominal_srate=0, channel_format='int16', source_id='TGAM_Attention')
outlet_attention = StreamOutlet(info_attention)

info_meditation = StreamInfo(name='Meditation', type='Markers', channel_count=1, nominal_srate=0, channel_format='int16', source_id='TGAM_Meditation')
outlet_meditation = StreamOutlet(info_meditation)

# 创建Qt信号类（修正信号定义）
class DataUpdateSignal(QtCore.QObject):
    update_signal = QtCore.pyqtSignal() 
signal = DataUpdateSignal()

class EEGThread(threading.Thread):
    def __init__(self, com_port="COM7"):
        super(EEGThread, self).__init__()
        self.com = com_port          # 串口号
        self.bps = 57600             # 波特率（需与设备一致）
        self.vaul = []               # 原始数据缓冲区
        self.error_count = 0         # 错误计数
        self.PARSER_SYNC_BYTE = 170  # 同步字节（0xAA）

    def run(self):
        global data, data2, data3
        try:
            with serial.Serial(self.com, self.bps, timeout=1) as t:
                print(f"连接到 {self.com}，波特率 {self.bps}")
                
                # 设备同步（仅保留前两个字节验证）
                print("等待设备同步...")
                while not stop_event.is_set():
                    # 严格匹配两个连续的0xAA
                    if t.read(1) == b'\xaa' and t.read(1) == b'\xaa':
                        print("同步成功")
                        break
                
                # 数据接收主循环（逐包处理）
                while not stop_event.is_set():
                    try:
                        # 1. 读取并验证同步头（AA AA）
                        if t.read(1) != b'\xaa' or t.read(1) != b'\xaa':
                            self.error_count += 1
                            continue  # 未找到同步头，跳过
                        
                        self.error_count = 0  # 重置错误计数
                        third_byte = t.read(1)  # 第三个字节（0x04或0x20）
                        if not third_byte:
                            continue
                        
                        # 2. 根据第三个字节处理数据包
                        if third_byte == b'\x04':  # 小包（原始数据）
                            packet = b'\xaa\xaa' + third_byte + t.read(5)  # 总长度8字节
                            if len(packet) != 8:
                                self.error_count += 1
                                continue
                            
                            # 解析字段
                            high = packet[5]
                            low = packet[6]
                            checksum_received = packet[7]
                            
                            # 校验和验证
                            sum_val = 0x80 + 0x02 + high + low
                            checksum_calculated = (~sum_val) & 0xFF
                            if checksum_calculated != checksum_received:
                                continue  # 校验失败，丢弃
                            
                            rawdata = (high << 8) | low
                            if rawdata > 32768:
                                rawdata -= 65536
                            self.vaul.append(rawdata)
                        
                        elif third_byte == b'\x20':  # 大包（状态数据）
                            packet = b'\xaa\xaa' + third_byte + t.read(32)  # 总长度35字节
                            if len(packet) != 35:
                                self.error_count += 1
                                continue
                            
                            # 解析专注度（索引32）和放松度（索引34）
                            attention = max(0, min(100, packet[32]))
                            meditation = max(0, min(100, packet[34]))
                            
                            # 更新数据并触发UI更新
                            data2.append(attention)
                            data3.append(meditation)
                            data = self.vaul.copy()
                            self.vaul = []
                            signal.update_signal.emit()
                        
                        else:  # 无效包类型
                            self.error_count += 1
                            print(f"无效第三字节: 0x{third_byte.hex()}")
                            if self.error_count > 10:
                                print("重置串口...")
                                t.reset_input_buffer()
                                self.error_count = 0
                        
                    except Exception as e:
                        print(f"数据处理异常: {e}")
                        time.sleep(0.1)

        # 小包（原始数据）解析后发送
            rawdata = (high << 8) | low
            if rawdata > 32768:
                rawdata -= 65536
            self.vaul.append(rawdata)
            # 发送原始EEG数据（假设单通道，每次发送一个样本）
            outlet_eeg.push_sample([rawdata])  # LSL要求每次发送一个样本数组

            # 大包（状态数据）解析后发送
            attention = max(0, min(100, packet[32]))
            meditation = max(0, min(100, packet[34]))
            outlet_attention.push_sample([attention])
            outlet_meditation.push_sample([meditation])
                        
        except Exception as e:
            print(f"线程错误: {e}")
        finally:
            print("数据采集线程已退出")

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('TGAM脑电波监测')
        self.resize(1000, 600)
        
        # 创建中央部件和布局
        self.central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QtWidgets.QVBoxLayout(self.central_widget)
        
        # 创建图表区域
        self.win = pg.GraphicsLayoutWidget()
        self.layout.addWidget(self.win)
        
        # 原始脑电波形图
        self.p6 = self.win.addPlot(title="原始脑电波形", row=0, col=0)
        self.p6.setYRange(-2000, 2000)
        self.p6.addLegend()
        self.curve = self.p6.plot(pen='y', name='原始数据')
        
        # 专注度/放松度趋势图
        self.p2 = self.win.addPlot(title="专注度/放松度趋势", row=1, col=0)
        self.p2.setYRange(0, 100)
        self.p2.addLegend()
        self.curve2 = self.p2.plot(pen=(0, 255, 0), name='放松度')
        self.curve3 = self.p2.plot(pen=(0, 0, 255), name='专注度')
        
        # 连接信号更新图表
        signal.update_signal.connect(self.update_plots)

    def update_plots(self):
        """实时更新图表数据"""
        self.curve.setData(data)
        self.curve2.setData(data3)  # 放松度对应data3（文档中大包第51字节为放松度值）
        self.curve3.setData(data2)  # 专注度对应data2（文档中大包第49字节为专注度值）
        print(f"更新数据 - 原始点数:{len(data)}, 专注度:{data2[-1] if data2 else 0}, 放松度:{data3[-1] if data3 else 0}")

if __name__ == '__main__':
    print("程序启动...")
    app = QtWidgets.QApplication(sys.argv)
    
    # 创建主窗口并显示
    main_window = MainWindow()
    main_window.show()
    
    # 创建并启动脑电数据采集线程（默认COM7，需根据实际设备修改）
    eeg_thread = EEGThread(com_port="COM7")
    eeg_thread.start()
    
    # 注册退出处理
    def on_exit():
        stop_event.set()
        eeg_thread.join()
        print("程序已安全退出")
    
    app.aboutToQuit.connect(on_exit)
    
    # 进入应用主循环
    sys.exit(app.exec_())