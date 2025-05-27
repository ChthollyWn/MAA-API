const { createApp, ref } = Vue;

const createTaskDialogVisible = ref(false)

const app = createApp({
    data() {
        return {
            createTaskDialogVisible: this.createTaskDialogVisible,
            pageTabActiveName: "Index",

            dailyTasks: {},
            adbScreenshot: "",
            pipeline: {
                status: 'idle',
                tasks: [],
                logs: []
            },
            createTasks: [
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
                    "select": [
                        1,
                        4,
                        5
                    ],
                    "confirm": [
                        2,
                        3,
                        4,
                        5
                    ],
                    "times": 4
                },
                {
                    "enable": false,
                    "name": "Infrast",
                    "facility": [
                        "Mfg",
                        "Trade",
                        "Power",
                        "Control",
                        "Reception",
                        "Office",
                        "Dorm"
                    ],
                    "drones": "Money"
                },
                {
                    "enable": false,
                    "name": "Fight",
                    "stage": "CE-6",
                    "medicine": 0,
                    "expiring_medicine": 0,
                    "series": 0
                },
                {
                    "enable": false,
                    "name": "Mall",
                    "shopping": true,
                    "buy_first": [
                        "招聘许可",
                        "龙门币"
                    ]
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
            ]
        }
    },
    methods: {
        getLogTagType(level) {
            const typeMap = {
                'info': 'info',
                'warning': 'warning',
                'error': 'danger'
            };
            return typeMap[level] || 'default';
        },

        getToken() {
            const urlParams = new URLSearchParams(window.location.search);
            return urlParams.get('token');
        },

        constructUrlWithToken(endpoint) {
            const token = this.getToken();
            let url = endpoint;
            if (token) {
                const urlParams = new URLSearchParams({ token });
                url += `?${urlParams.toString()}`;
            }
            return url;
        },

        getTagType(status) {
            switch (status) {
                case 'idle':
                    return 'info';
                case 'running':
                    return 'primary';
                case 'completed':
                    return 'success';
                case 'failed':
                    return 'danger';
                case 'cancelled':
                    return 'warning';
                default:
                    return 'info';
            }
        },

        goToIndex() {
            token = this.getToken();
            url = '/'
            if (token) {
                url += `?token=${token}`
            }
            window.location.href = url;
        },

        goToDaily() {
            token = this.getToken();
            url = 'daily'
            if (token) {
                url += `?token=${token}`
            }
            window.location.href = url;
        },

        handleTabClick(tab) {
            this.pageTabActiveName = tab.paneName;
            localStorage.setItem('pageTabActiveName', this.pageTabActiveName);

            if (tab.paneName === 'Index') {
                this.goToIndex()
            } else if (tab.paneName === 'Daily') {
                this.goToDaily()
            }
        },

        fetchDailyTaskData() {
            const url = this.constructUrlWithToken('/api/maa/daily')
            axios.get(url)
                .then(resp => {
                    if (resp.data.code === 10200) {
                        this.dailyTasks = resp.data.data;
                        this.$notify({
                            title: '成功',
                            message: '日常任务数据获取成功',
                            position: 'bottom-right',
                            type: 'success'
                        });
                    } else {
                        this.$notify.error({
                            title: '错误',
                            message: resp.data.message,
                            position: 'bottom-right'
                        });
                    }

                })
                .catch(err => {
                    console.error("fetchDailyTaskData 异常 ", err);
                    this.$notify.error({
                        title: '错误',
                        message: '获取日常任务数据失败',
                        position: 'bottom-right',
                    });
                });
        },

        updateDailyTaskData() {
            this.$confirm('确定更新日常任务？')
                .then(_ => {
                    const url = this.constructUrlWithToken('/api/maa/daily')
                    axios.put(url, this.dailyTasks)
                        .then(resp => {
                            if (resp.data.code === 10200) {
                                this.$notify({
                                    title: '成功',
                                    message: '日常任务数据更新成功',
                                    position: 'bottom-right',
                                    type: 'success'
                                });
                            } else {
                                this.$notify.error({
                                    title: '错误',
                                    message: resp.data.message,
                                    position: 'bottom-right'
                                });
                            }
                        })
                        .catch(err => {
                            console.error("updateDailyTaskData 异常 ", err);
                            this.$notify.error({
                                title: '错误',
                                message: '更新日常任务数据失败',
                                position: 'bottom-right'
                            });
                        });
                })
                .catch();
        },

        executeDailyTaskData() {
            this.$confirm('确定立即执行日常任务？')
                .then(_ => {
                    const url = this.constructUrlWithToken('/api/maa/daily/execute')
                    axios.post(url)
                        .then(resp => {
                            if (resp.data.code === 10200) {
                                this.$notify({
                                    title: '成功',
                                    message: '日常任务执行成功',
                                    position: 'bottom-right',
                                    type: 'success'
                                });
                            } else {
                                this.$notify.error({
                                    title: '错误',
                                    message: resp.data.message,
                                    position: 'bottom-right'
                                });
                            }
                        })
                        .catch(err => {
                            console.error("executeDailyTaskData 异常 ", err);
                            this.$notify.error({
                                title: '错误',
                                message: '执行日常任务失败',
                                position: 'bottom-right'
                            });
                        });
                })
                .catch();
        },

        fetchAdbScreenshot() {
            const url = this.constructUrlWithToken('/api/adb/screenshot')
            axios.get(url, {
                responseType: 'arraybuffer'
            })
                .then(resp => {
                    const base64Image = btoa(
                        new Uint8Array(resp.data)
                            .reduce((data, byte) => data + String.fromCharCode(byte), '')
                    );

                    this.adbScreenshot = `data:image/jpeg;base64,${base64Image}`

                    this.$notify({
                        title: '成功',
                        message: 'ADB 截屏成功',
                        position: 'bottom-right',
                        type: 'success'
                    });
                })
                .catch(err => {
                    console.error("ADB 截屏异常 ", err);
                    this.$notify.error({
                        title: '错误',
                        message: 'ADB 截屏失败',
                        position: 'bottom-right',
                    });
                })
        },

        fetchMaaPipelineData() {
            const url = this.constructUrlWithToken('/api/maa/pipeline')
            axios.get(url)
                .then(resp => {
                    if (resp.data.code === 10200) {
                        this.pipeline = resp.data.data;
                        this.$notify({
                            title: '成功',
                            message: '流水线数据获取成功',
                            position: 'bottom-right',
                            type: 'success'
                        });
                    } else {
                        this.$notify.error({
                            title: '错误',
                            message: resp.data.message,
                            position: 'bottom-right'
                        });
                    }
                })
                .catch(err => {
                    console.error("fetchMaaPipelineData 异常 ", err);
                    this.$notify.error({
                        title: '错误',
                        message: '获取流水线数据失败',
                        position: 'bottom-right'
                    });
                });
        },

        cancelMaaPipelineData() {
            this.$confirm('确定停止流水线任务？')
                .then(_ => {
                    const url = this.constructUrlWithToken('/api/maa/pipeline')
                    axios.delete(url)
                        .then(resp => {
                            if (resp.data.code === 10200) {
                                this.$notify({
                                    title: '成功',
                                    message: '流水线任务停止成功',
                                    position: 'bottom-right',
                                    type: 'success'
                                });
                            } else {
                                this.$notify.error({
                                    title: '错误',
                                    message: resp.data.message,
                                    position: 'bottom-right'
                                });
                            }
                        })
                        .catch(err => {
                            console.error("fetchMaaPipelineData 异常 ", err);
                            this.$notify.error({
                                title: '错误',
                                message: '流水线任务停止失败',
                                position: 'bottom-right'
                            });
                        });
                })
                .catch();
        },

        executeMaaPipelineData() {
            this.$confirm('确定立即执行流水线任务？')
                .then(_ => {

                    const enableTasks = [];
                    for (const task of this.createTasks) {
                        if (task.enable) {
                            enableTasks.push(task);
                        }
                    }
                    if (enableTasks.length === 0) {
                        return;
                    }

                    const url = this.constructUrlWithToken('/api/maa/pipeline')
                    axios.post(url, enableTasks)
                        .then(resp => {
                            if (resp.data.code === 10200) {
                                this.$notify({
                                    title: '成功',
                                    message: '流水线任务执行成功',
                                    position: 'bottom-right',
                                    type: 'success'
                                });
                            } else {
                                this.$notify.error({
                                    title: '错误',
                                    message: resp.data.message,
                                    position: 'bottom-right'
                                });
                            }
                            this.fetchMaaPipelineData();
                            this.createTaskDialogVisible = false
                        })
                        .catch(err => {
                            console.error("fetchMaaPipelineData 异常 ", err);
                            this.$notify.error({
                                title: '错误',
                                message: '流水线任务执行失败',
                                position: 'bottom-right'
                            });
                        });
                })
                .catch();
        },

        refreshMaaPipelineDataAndScreenshot() {
            this.fetchAdbScreenshot();
            this.fetchMaaPipelineData();
        }
    },
    created() {
        this.fetchDailyTaskData();
        this.fetchAdbScreenshot();
        this.fetchMaaPipelineData();
    },

    mounted() {
        const storedTab = localStorage.getItem('pageTabActiveName');
        if (storedTab) {
          this.pageTabActiveName = storedTab;
        }
    }
});

app.use(ElementPlus);
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
    app.component(key, component)
}
app.mount('#app');