const { createApp } = Vue;

const app = createApp({
    data() {
        return {
            status: '',
            tasks: [],
            logs: []
        };
    },
    methods: {
        fetchData() {
            fetch('http://localhost:8000/api/maa/pipeline')
                .then(response => response.json())
                .then(data => {
                    if (data.code === 10200) {
                        this.status = data.data.status;
                        this.tasks = data.data.tasks;
                        this.logs = data.data.logs;
                    } else {
                        console.error('Error fetching data:', data.message);
                    }
                })
                .catch(error => {
                    console.error('Fetch error:', error);
                });
        },
        sendPostRequest() {
            fetch('http://localhost:8000/api/maa/pipeline', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    action: 'start'
                })
            })
            .then(response => response.json())
            .then(data => {
                console.log('POST response:', data);
            })
            .catch(error => {
                console.error('POST request error:', error);
            });
        }
    },
    mounted() {
        this.fetchData();
        setInterval(this.fetchData, 10000);
    }
});

// 使用 Element Plus
app.use(ElementPlus);

// 挂载 Vue 应用
app.mount('#app');