const { createApp } = Vue;

const app = createApp({
    data() {
        return {
            dailyTasks: {}
        }
    },
    methods: {
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
        fetchDailyTaskData() {
            const url = this.constructUrlWithToken('/api/maa/daily')
            axios.get(url)
                .then(resp => {
                    if (resp.data.code === 10200) {
                        this.dailyTasks = resp.data.data;
                        this.$notify({
                            title: '成功',
                            message: '日常任务数据获取成功',
                            type: 'success'
                        });
                    } else {
                        this.$notify.error({
                            title: '错误',
                            message: resp.data.message
                        });
                    }

                })
                .catch(err => {
                    console.error("fetchDailyTaskData 异常 ", err);
                    this.$notify.error({
                        title: '错误',
                        message: '获取日常任务数据失败'
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
                                    type: 'success'
                                });
                            } else {
                                this.$notify.error({
                                    title: '错误',
                                    message: resp.data.message
                                });
                            }
                        })
                        .catch(err => {
                            console.error("updateDailyTaskData 异常 ", err);
                            this.$notify.error({
                                title: '错误',
                                message: '更新日常任务数据失败'
                            });
                        });
                })
                .catch(() => { })
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
                                    type: 'success'
                                });
                            } else {
                                this.$notify.error({
                                    title: '错误',
                                    message: resp.data.message
                                });
                            }
                        })
                        .catch(err => {
                            console.error("executeDailyTaskData 异常 ", err);
                            this.$notify.error({
                                title: '错误',
                                message: '执行日常任务失败'
                            });
                        });
                })
                .catch(() => { })
        }
    },
    created() {
        this.fetchDailyTaskData();
    }
});

// 使用 Element Plus
app.use(ElementPlus);

// 挂载 Vue 应用
app.mount('#app');