const { createApp, ref, reactive, onMounted, computed } = Vue;

// 注册所有图标
const registerIcons = (app) => {
    for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
        app.component(key, component);
    }
};

// Axios 全局拦截器（支持 Bearer Token）
axios.interceptors.request.use(config => {
    const token = localStorage.getItem('maa_token');
    if (token) {
        config.headers['Authorization'] = `Bearer ${token}`;
    }
    return config;
});

axios.interceptors.response.use(response => response, error => {
    if (error.response && error.response.status === 401) {
        ElementPlus.ElMessage.error("鉴权失败：Token 无效或未提供");
    }
    return Promise.reject(error);
});

const app = createApp({
    setup() {
        const activeMenu = ref(localStorage.getItem('activeMenu') || 'pipeline');
        const authDialogVisible = ref(false);
        const tokenInput = ref(localStorage.getItem('maa_token') || '');

        const startTaskDialogVisible = ref(false); // 新增控制启动弹窗

        const pipeline = reactive({
            status: 'idle',
            tasks: [],
            logs: []
        });

        // 默认新建任务模板列表
        const createTasks = reactive([
                {
                    "enable": false,
                    "name": "StartUp",
                    "client_type": "Bilibili",
                    "start_game_enabled": true,
                },
                {
                    "enable": false,
                    "name": "Recruit",
                    "refresh": true,
                    "select": [1, 4, 5],
                    "confirm": [2, 3, 4, 5],
                    "times": 4
                },
                {
                    "enable": false,
                    "name": "Infrast",
                    "facility": ["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"],
                    "drones": "Money",
                    "replenish": false
                },
                {
                    "enable": false,
                    "name": "Fight",
                    "stage": "1-7",
                    "medicine": 0,
                    "expiring_medicine": 0,
                    "series": 0
                },
                {
                    "enable": false,
                    "name": "Mall",
                    "shopping": true,
                    "buy_first": ["招聘许可", "龙门币"]
                },
                {
                    "enable": false,
                    "name": "Award"
                },
                {
                    "enable": false,
                    "name": "CloseDown",
                    "client_type": "Bilibili"
                }
        ]);

        const dailyTasks = ref('');
        const adbScreenshot = ref('');
        const dailyDataObject = ref({}); 
        const dailyConfigEditorStr = ref('');
        const systemLogs = ref([]);
        const logContainer = ref(null);
        let logTimer = null;

        const updateVersions = ref({}); // 新增：储存各模块的版本信息
        const updateClientType = ref('Bilibili'); // 新增：默认的客户端类型为 Bilibili
        
        const fetchUpdateVersions = async () => {
            try {
                const res = await axios.get('/api/update/versions', { params: { client_type: updateClientType.value } });
                if (res.data && res.data.code === 10200) {
                    updateVersions.value = res.data.data;
                }
            } catch (err) {
                console.error("Fetch Update Versions Error: ", err);
            }
        };

        const handleMenuSelect = (index) => {
            activeMenu.value = index;
            localStorage.setItem('activeMenu', index);

            // 清除之前的定时器
            if (logTimer) {
                clearInterval(logTimer);
                logTimer = null;
            }

            if (index === 'daily') {
                fetchDailyTaskData();
            } else if (index === 'pipeline') {
                refreshMaaPipelineDataAndScreenshot();
                setTimeout(() => { fetchScreenshot(); }, 500); 
            } else if (index === 'logs') {
                fetchSystemLogs();
                logTimer = setInterval(fetchSystemLogs, 3000);
            } else if (index === 'update') {
                fetchUpdateVersions(); // 新增：切换到热部署时探测版本
            }
        };

        const saveToken = () => {
            localStorage.setItem('maa_token', tokenInput.value);
            authDialogVisible.value = false;
            ElementPlus.ElMessage.success("Token 已保存！");
            handleMenuSelect(activeMenu.value);
        };

        const fetchScreenshot = () => {
            const tokenUrlParam = tokenInput.value ? `?token=${tokenInput.value}&` : '?';
            adbScreenshot.value = `/api/adb/screenshot${tokenUrlParam}t=${new Date().getTime()}`;
        };

        const getTagType = (status) => {
            const map = { 'idle': 'info', 'running': 'primary', 'completed': 'success', 'failed': 'danger', 'cancelled': 'warning' };
            return map[status] || 'info';
        };

        const getLogTagType = (level) => {
            const typeMap = { 'info': 'info', 'warning': 'warning', 'error': 'danger' };
            return typeMap[level] || 'default';
        };

        const refreshMaaPipelineDataAndScreenshot = async () => {
            try {
                const res = await axios.get('/api/maa/pipeline');
                if (res.data && res.data.code === 10200) {
                    const pipelineData = res.data.data;
                    if (pipelineData) {
                        pipeline.status = pipelineData.status || 'idle';
                        // 后端的 task_list 可能会是字典也有可能是数组，所以这里要兼容下
                        let parsedTasks = [];
                        if (pipelineData.tasks) {
                            parsedTasks = Array.isArray(pipelineData.tasks) ? pipelineData.tasks : Object.values(pipelineData.tasks);
                        } else if (pipelineData.task_list) {
                            parsedTasks = Array.isArray(pipelineData.task_list) ? pipelineData.task_list : Object.values(pipelineData.task_list);
                        }
                        pipeline.tasks = parsedTasks;
                        pipeline.logs = pipelineData.logs || [];
                    }
                }
            } catch (error) {
                console.error("Fetch Pipeline Data Error: ", error);
            }
        };

        const getWeekdayName = (idx) => {
            const map = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
            return map[parseInt(idx)] || `Day ${idx}`;
        };

        const groupStates = reactive({}); // 新增：用于独立维护队列组别的 UI 折叠状态

        const toggleGroupState = (dictKey) => {
            groupStates[dictKey] = !groupStates[dictKey];
        };

        const fetchDailyTaskData = async () => {
            try {
                const res = await axios.get('/api/maa/daily');
                if (res.data && res.data.code === 10200) {
                    const data = res.data.data;
                    // 初始化时确保 time 为数组，支持多个执行时间点
                    if (data && !Array.isArray(data.time)) {
                        data.time = data.time ? [data.time] : ['04:00'];
                    }

                    // 为 UI 层的结构注入展开收起的内部状态
                    if (data && data.task_dict) {
                        for (let k in data.task_dict) {
                            if (typeof data.task_dict[k] === 'object' && !Array.isArray(data.task_dict[k])) {
                                // 万一是老版本的数据格式错误，修正为数组
                            }
                            
                            // 初始化当前这组队列在全局面板中的展开收起状态 (默认全部收起)
                            if (groupStates[k] === undefined) {
                                groupStates[k] = false;
                            }

                            if (Array.isArray(data.task_dict[k])) {
                                data.task_dict[k].forEach((t, index) => {
                                    // 节点级别全部收起
                                    t.isExpanded = false;
                                });
                            }
                        }
                    }

                    dailyDataObject.value = data || {}; 
                    if (data && data.task_dict) {
                        dailyConfigEditorStr.value = JSON.stringify(data.task_dict, null, 4);
                    }
                }
            } catch (err) {
                console.error("Fetch Daily Task Data Error: ", err);
            }
        };

        const availableTaskKeys = computed(() => {
            try {
                if (dailyDataObject.value && dailyDataObject.value.task_dict) {
                    return Object.keys(dailyDataObject.value.task_dict);
                }
            } catch (e) {}
            return [];
        });

        const updateDailyTaskData = async () => {
            try {
                let payload = JSON.parse(JSON.stringify(dailyDataObject.value));
                const res = await axios.put('/api/maa/daily', payload);
                if (res.data && res.data.code === 10200) {
                    ElementPlus.ElMessage.success('全局排班设置与字典已持久化并保写入！');
                }
            } catch (err) { 
                ElementPlus.ElMessage.error('排班保存失败：' + err);
            }
        };

        const addTaskDictRow = () => {
            if (!dailyDataObject.value.task_dict) {
                dailyDataObject.value.task_dict = {};
            }
            const defaultKeyName = `new_task_${Date.now()}`;
            dailyDataObject.value.task_dict[defaultKeyName] = [
                {
                    "name": "StartUp",
                    "client_type": "Bilibili",
                    "isExpanded": false
                }
            ];
            groupStates[defaultKeyName] = false; // 新增队列时也默认收起
        };

        const deleteTaskDictRow = (key) => {
            if (!dailyDataObject.value.task_dict) return;
            delete dailyDataObject.value.task_dict[key];
            delete groupStates[key]; // 清理状态缓存
        };

        const renameTaskDictKey = (oldKey, newKey) => {
            if (!newKey || newKey === oldKey || !dailyDataObject.value.task_dict) return;
            if (dailyDataObject.value.task_dict[newKey]) {
                ElementPlus.ElMessage.warning('该队列代号已存在，无法重命名');
                return;
            }
            const data = dailyDataObject.value.task_dict[oldKey];
            delete dailyDataObject.value.task_dict[oldKey];
            dailyDataObject.value.task_dict[newKey] = data;

            // 迁移状态缓存
            const state = groupStates[oldKey];
            delete groupStates[oldKey];
            groupStates[newKey] = state !== undefined ? state : true;
        };

        const updateTaskDictArray = (key, val) => {
            try {
                const parsedArray = JSON.parse(val);
                if (Array.isArray(parsedArray)) {
                    dailyDataObject.value.task_dict[key] = parsedArray;
                } else {
                    ElementPlus.ElMessage.warning('内部任务集合必须是 JSON 数组结构');
                }
            } catch (e) {
                ElementPlus.ElMessage.error('JSON 语法错误或大括号缺漏！该值未保存');
            }
        };

        const addTaskToDict = (dictKey) => {
            if (!dailyDataObject.value.task_dict[dictKey]) return;
            dailyDataObject.value.task_dict[dictKey].push({
                name: "Fight", // 给个默认填充
                isExpanded: true // 默认展开
            });
        };

        const removeTaskFromDict = (dictKey, tIndex) => {
            if (!dailyDataObject.value.task_dict[dictKey]) return;
            dailyDataObject.value.task_dict[dictKey].splice(tIndex, 1);
        };

        const updateSingleTaskJson = (dictKey, tIndex, val) => {
            try {
                const parsed = JSON.parse(val);
                Object.assign(dailyDataObject.value.task_dict[dictKey][tIndex], parsed);
                // 这里为保证响应式视图更新，做一次深拷贝替换（如果视图由于复杂深度没更新的话）
                // dailyDataObject.value.task_dict[dictKey].splice(tIndex, 1, dailyDataObject.value.task_dict[dictKey][tIndex]);
            } catch (e) {
                ElementPlus.ElMessage.error('JSON 底层扩展参数格式错误！该项未保存');
            }
        };

        const startPipeline = async () => {
            try {
                const tasksToStart = createTasks.filter(item => item.enable);
                if (tasksToStart.length === 0) {
                    ElementPlus.ElMessage.warning('请至少勾选一位要调度或委派的任务阶段！');
                    return;
                }
                const res = await axios.post('/api/maa/pipeline', tasksToStart);
                if (res.data.code === 10200) {
                    ElementPlus.ElMessage.success('多步行动调度管架任务注入并投射完毕！');
                    startTaskDialogVisible.value = false;
                    setTimeout(refreshMaaPipelineDataAndScreenshot, 1000);
                }
            } catch (err) { }
        };

        const stopPipeline = async () => {
            try{
                const res = await axios.delete('/api/maa/pipeline');
                if (res.data.code === 10200) {
                    ElementPlus.ElMessage.warning('已向当前进行中的任务流广播了退出中断讯号（Abort）');
                    setTimeout(refreshMaaPipelineDataAndScreenshot, 1500);
                }
            }catch(err){}
        };

        const triggerDaily = async () => {
            try {
                const res = await (axios.post('/api/maa/daily/execute'));
                if (res.data && res.data.code === 10200) {
                    ElementPlus.ElMessage.success('已模拟每天的后台唤醒发信。由于涉及多进程管道，日志稍后回显。');
                }
            } catch (err) { }
        };

        const doUpdate = async (type) => {
            try {
                const res = await axios.post(`/api/update/${type}`);
                if (res.data && res.data.code === 10200) {
                    ElementPlus.ElMessage.success(res.data.message);
                }
            } catch (err) { 
                console.error("Update Trigger Error: ", err);
            }
        };

        const executeCommand = async (command) => {
            try {
                let res;
                if (command === 'start') {
                    // 发送被启用的任务数组
                    const tasksToStart = createTasks.filter(item => item.enable);
                    if (tasksToStart.length === 0) {
                        ElementPlus.ElMessage.warning('请至少勾选一个任务！');
                        return;
                    }
                    res = await axios.post('/api/maa/pipeline', tasksToStart);
                    if (res.data.code === 10200) {
                        ElementPlus.ElMessage.success('流水线启动成功！');
                        startTaskDialogVisible.value = false;
                        setTimeout(refreshMaaPipelineDataAndScreenshot, 1000);
                    }
                } else if (command === 'stop') {
                    res = await axios.delete('/api/maa/pipeline');
                    if (res.data.code === 10200) {
                        ElementPlus.ElMessage.success('已发送停止指令！');
                        setTimeout(refreshMaaPipelineDataAndScreenshot, 1500);
                    }
                } else if (command === 'daily') {
                    res = await axios.post('/api/maa/daily/execute');
                    if (res.data.code === 10200) {
                        ElementPlus.ElMessage.success('临时唤起日常配置任务成功！');
                    }
                }
            } catch (err) {
                 ElementPlus.ElMessage.error(`操作失败: ` + err);
            }
        };

        const fetchSystemLogs = async () => {
            try {
                const res = await axios.get('/api/system/logs');
                if (res.data && res.data.code === 10200) {
                    systemLogs.value = res.data.data;
                    setTimeout(() => {
                        if (logContainer.value) {
                            logContainer.value.scrollTop = logContainer.value.scrollHeight;
                        }
                    }, 50);
                }
            } catch (err) { 
                console.error("Fetch System Logs Error: ", err);
            }
        };

        onMounted(() => {
            if (!tokenInput.value) {
                authDialogVisible.value = true;
            }
            handleMenuSelect(activeMenu.value);
        });

        return {
            activeMenu, authDialogVisible, tokenInput, createTasks, startTaskDialogVisible, 
            pipeline, dailyTasks, adbScreenshot, dailyDataObject, dailyConfigEditorStr,
            systemLogs, logContainer, availableTaskKeys, updateVersions,
            handleMenuSelect, saveToken, getTagType, getLogTagType, refreshMaaPipelineDataAndScreenshot,
            fetchDailyTaskData, updateDailyTaskData, triggerDaily, doUpdate, fetchSystemLogs, fetchScreenshot,
            startPipeline, stopPipeline, getWeekdayName, executeCommand,
            addTaskDictRow, deleteTaskDictRow, renameTaskDictKey, updateTaskDictArray,
            addTaskToDict, removeTaskFromDict, updateSingleTaskJson, groupStates, toggleGroupState
        };
    }
});

registerIcons(app);
app.use(ElementPlus);
app.mount('#app');