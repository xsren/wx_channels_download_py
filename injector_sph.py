import os
import re
import json
from mitmproxy import http

class WeixinChannelInterceptor:
    def __init__(self):
        self.v = "?t=250215"  # 缓存控制参数
        self.script_map = {
            "/web/pages/feed": "main.js",
            "/web/pages/home": "main.js",
            "/web/pages/profile": "main.js",
            "/web/pages/s": "main.js",
            "/t/wx_fed/cdn_libs/res/FileSaver.min.js": "FileSaver.min.js",
            "/t/wx_fed/cdn_libs/res/jszip.min.js": "jszip.min.js",
        }
        # 初始化时加载所有脚本
        self._load_scripts()

    def _load_scripts(self):
        """从文件系统加载JS脚本内容"""
        script_dir = os.path.join(os.path.dirname(__file__), "inject")  # 假设脚本存放在scripts目录

        for path, filename in self.script_map.items():
            file_path = os.path.join(script_dir, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    self.script_map[path] = f.read()  # 用文件内容替换文件名
            except FileNotFoundError:
                print(f"警告：脚本文件 {filename} 未找到，保留空内容")
                self.script_map[path] = ""

    def request(self, flow: http.HTTPFlow):
        if "channels.weixin.qq.com" not in flow.request.host and "res.wx.qq.com" not in flow.request.host:
            return

        if "/web/report-" in flow.request.path:
            return
        # logger.info(f"request flow:{flow}")

        # 删除Accept-Encoding头[3](@ref)
        flow.request.headers.pop("Accept-Encoding", None)

        # 处理需要拦截的JS请求[5](@ref)
        path = flow.request.path
        if any(key in path for key in ["jszip", "FileSaver.min"]):
            local_js_content = self.script_map[path]
            self._inject_script_response(flow, "application/javascript", local_js_content)
        elif path == "/__wx_channels_api/profile":
            self._handle_profile_api(flow)
        elif path == "/__wx_channels_api/tip":
            self._handle_tip_api(flow)

    def response(self, flow: http.HTTPFlow):
        if "channels.weixin.qq.com" not in flow.request.host and "res.wx.qq.com" not in flow.request.host:
            return
        if "/web/report-" in flow.request.path:
            return
        # logger.info(f"response flow:{flow}")
        # HTML内容注入[3,5](@ref)
        if "text/html" in flow.response.headers.get("content-type", ""):
            self._inject_html_scripts(flow)

        # JS内容修改[3](@ref)
        if "application/javascript" in flow.response.headers.get("content-type", ""):
            self._modify_javascript(flow)

    def _inject_script_response(self, flow, content_type, script_content):
        if "Range" in flow.request.headers:
            # 返回 206 分块响应（需根据实际内容处理）
            flow.response = http.Response.make(
                206,
                script_content,
                {"Content-Type": content_type}
            )
        else:
            flow.response = http.Response.make(
                200,
                script_content,
                {"Content-Type": content_type, "X-Debug": "local_file"}
            )
        #
        # flow.response = http.Response.make(
        #     200,
        #     script_content,
        #     {"Content-Type": content_type, "X-debug": "local_file"}
        # )
        # flow.kill()  # 终止原始请求链[5](@ref)

    def _handle_profile_api(self, flow):
        try:
            data = json.loads(flow.request.content)
            print(f"\n打开了视频\n{data.get('title', '')}\n")
            flow.response = http.Response.make(
                200,
                b"{}",
                {"Content-Type": "application/json", "__debug": "fake_resp"}
            )
        except json.JSONDecodeError:
            print("解析profile请求失败")

    def _handle_tip_api(self, flow):
        try:
            data = json.loads(flow.request.content)
            print(f"[FRONTEND]{data.get('msg', '')}")
            flow.response = http.Response.make(
                200,
                b"{}",
                {"Content-Type": "application/json", "__debug": "fake_resp"}
            )
        except json.JSONDecodeError:
            print("解析tip请求失败")

    def _inject_html_scripts(self, flow):
        html = flow.response.text

        # # 处理src属性中的.js文件
        # script_reg1 = re.compile(r'src="([^"]+)\.js"')
        # html = script_reg1.sub(r'src="\g<1>.js' + self.v + '"', html)
        #
        # # 处理href属性中的.js文件
        # script_reg2 = re.compile(r'href="([^"]+)\.js"')
        # html = script_reg2.sub(r'href="\g<1>.js' + self.v + '"', html)

        # 第一个正则替换：匹配src属性中的JS文件并添加版本号
        script_reg1 = re.compile(r'src="([^"]{1,})\.js"')
        html = script_reg1.sub(r'src="\1.js' + self.v + r'"', html)

        # 第二个正则替换：匹配href属性中的JS文件并添加版本号
        script_reg2 = re.compile(r'href="([^"]{1,})\.js"')
        html = script_reg2.sub(r'href="\1.js' + self.v + r'"', html)

        # 注入脚本标签[3](@ref)
        simple_path = flow.request.path.split('?')[0]
        if flow.request.host == "channels.weixin.qq.com" and simple_path in self.script_map:
            script = f"<script>{self.script_map[simple_path]}</script>"
            html = html.replace("<head>", f"<head>\n{script}")
            flow.response.headers["__debug"] = "append_script"
            print(f"成功注入 {simple_path} 页面脚本")

        flow.response.text = html

    def _modify_javascript(self, flow):
        content = flow.response.text
        path = flow.request.path

        # # 定义四组正则表达式
        # dep_reg = re.compile(r'"js/([^"]+)\.js"')  # 路径类引用
        # from_reg = re.compile(r'from\s*"([^"]+)\.js"')  # ES6模块from导入
        # lazy_import_reg = re.compile(r'import$"([^"]+)\.js"$')  # 动态import
        # import_reg = re.compile(r'import\s*"([^"]+)\.js"')  # 静态import
        #
        # # 执行替换（按依赖顺序）
        # content = from_reg.sub(rf'from "\g<1>.js?v={self.v}"', content)  # 注意分组引用用\g<1>
        # content = dep_reg.sub(rf'"js/\g<1>.js?v={self.v}"', content)
        # content = lazy_import_reg.sub(rf'import("\g<1>.js?v={self.v}")', content)
        # content = import_reg.sub(rf'import "\g<1>.js?v={self.v}"', content)

        # 定义正则表达式模式
        dep_reg = re.compile(r'"js/([^"]{1,})\.js"')
        from_reg = re.compile(r'from {0,1}"([^"]{1,})\.js"')
        lazy_import_reg = re.compile(r'import\("([^"]{1,})\.js"\)')
        import_reg = re.compile(r'import {0,1}"([^"]{1,})\.js"')

        # 使用正则表达式进行替换
        content = from_reg.sub(r'from"\1.js' + self.v + r'"', content)
        content = dep_reg.sub(r'"js/\1.js' + self.v + r'"', content)
        content = lazy_import_reg.sub(r'import("\1.js' + self.v + r'")', content)
        content = import_reg.sub(r'import"\1.js' + self.v + r'"', content)

        content = self.modify_js_1(path, content)
        content = self.modify_js_2(path, content)
        content = self.modify_js_3(path, content)
        content = self.modify_js_4(path, content)

        flow.response.headers["__debug"] = "replace_script"
        flow.response.text = content

    def modify_js_1(self, path, content):
        if "/t/wx_fed/finder/web/web-finder/res/js/index.publish" in path:
            print(f"拦截 {path}")

            # 第一次正则替换
            regex1 = re.compile(r'this\.sourceBuffer\.appendBuffer$h$,')
            regex1 = re.compile(r'this\.sourceBuffer\.appendBuffer\(h\)')
            regex1 = re.compile(r'this\.sourceBuffer\.appendBuffer\(h\),')
            replace_str1 = """(() => {
        if (window.__wx_channels_store__) {
        window.__wx_channels_store__.buffers.push(h);
        }
        })(),this.sourceBuffer.appendBuffer(h),"""
            if regex1.search(content):
                print("2. 视频播放 js 修改成功")
            content = regex1.sub(replace_str1, content)

            # 第二次正则替换
            # regex2 = re.compile(r'if\(f\.cmd===re\.MAIN_THREAD_CMD\.AUTO_CUT')
            regex2 = re.compile(r'if\(f\.cmd===re\.MAIN_THREAD_CMD\.AUTO_CUT')

            replace_str2 = """if(f.cmd==="CUT"){
            if (window.__wx_channels_store__) {
            console.log("CUT", f, __wx_channels_store__.profile.key);
            window.__wx_channels_store__.keys[__wx_channels_store__.profile.key]=f.decryptor_array;
            }
        }
        if(f.cmd===re.MAIN_THREAD_CMD.AUTO_CUT"""
            content = regex2.sub(replace_str2, content)
        return content

    def modify_js_2(self, path, content):
        if "/t/wx_fed/finder/web/web-finder/res/js/virtual_svg-icons-register" in path:
            print(f"拦截 {path}")
            # 第一个正则替换
            # regexp1 = re.compile(r'async finderGetCommentDetail$(\w+)$\{return(.*?)\}async', re.DOTALL)
            regexp1 = re.compile(r'async finderGetCommentDetail\((\w+)\)\{return(.*?)\}async')

            replace_str1 = r'''async finderGetCommentDetail(\g<1>) {
            var feedResult = await\g<2>;
            var data_object = feedResult.data.object;
            if (!data_object.objectDesc) {
                return feedResult;
            }
            var media = data_object.objectDesc.media[0];
            var profile = media.mediaType !== 4 ? {
                type: "picture",
                id: data_object.id,
                title: data_object.objectDesc.description,
                files: data_object.objectDesc.media,
                spec: [],
                contact: data_object.contact
            } : {
                type: "media",
                duration: media.spec[0].durationMs,
                spec: media.spec,
                title: data_object.objectDesc.description,
                coverUrl: media.coverUrl,
                url: media.url+media.urlToken,
                size: media.fileSize,
                key: media.decodeKey,
                id: data_object.id,
                nonce_id: data_object.objectNonceId,
                nickname: data_object.nickname,
                createtime: data_object.createtime,
                fileFormat: media.spec.map(o => o.fileFormat),
                contact: data_object.contact
            };
            fetch("/__wx_channels_api/profile", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(profile)
            });
            if (window.__wx_channels_store__) {
                __wx_channels_store__.profile = profile;
                window.__wx_channels_store__.profiles.push(profile);
            }
            return feedResult;
        }async'''
            if regexp1.search(content):
                print("3. 视频详情页 js 修改成功")
            content = regexp1.sub(replace_str1, content)

            # 第二个正则替换
            regex2 = re.compile(r'r\.default=\{dialog')
            content = regex2.sub(r'r.default=window.window.__wx_channels_tip__={dialog', content)

            # 第三个正则替换
            regex3 = re.compile(r'const u=this\.storage\.getSession')
            content = regex3.sub(r'return;const u = this.storage.getSession', content)

            # 第四个正则替换
            regex4 = re.compile(r'return this\.storage\.getSession')
            content = regex4.sub(r'return null;return this.storage.getSession', content)

            # 第五个正则替换
            # regex5 = re.compile(r'this\.updateDetail$o$')
            regex5 = re.compile(r'this\.updateDetail\(o\)')
            replace_str5 = r'''(() => {
            if (Object.keys(o).length===0){
                return;
            }
            var data_object = o;
            var media = data_object.objectDesc.media[0];
            var profile = media.mediaType !== 4 ? {
                type: "picture",
                id: data_object.id,
                title: data_object.objectDesc.description,
                files: data_object.objectDesc.media,
                spec: [],
                contact: data_object.contact
            } : {
                type: "media",
                duration: media.spec[0].durationMs,
                spec: media.spec,
                title: data_object.objectDesc.description,
                url: media.url+media.urlToken,
                size: media.fileSize,
                key: media.decodeKey,
                id: data_object.id,
                nonce_id: data_object.objectNonceId,
                nickname: data_object.nickname,
                createtime: data_object.createtime,
                fileFormat: media.spec.map(o => o.fileFormat),
                contact: data_object.contact
            };
            if (window.__wx_channels_store__) {
                window.__wx_channels_store__.profiles.push(profile);
            }
        })(),this.updateDetail(o)'''
            content = regex5.sub(replace_str5, content)
        return content

    def modify_js_3(self, path, content):
        if "/t/wx_fed/finder/web/web-finder/res/js/FeedDetail.publish" in path:
            print(f"拦截 {path}")
            regex = re.compile(r',"投诉"\)]')
            replace_str = '''","投诉"),...(() => {
            if (window.__wx_channels_store__ && window.__wx_channels_store__.profile) {
                return window.__wx_channels_store__.profile.spec.map((sp) => {
                    return p("div",{class:"context-item",role:"button",onClick:() => __wx_channels_handle_click_download__(sp)},sp.fileFormat);
                });
            }
            })(),p("div",{class:"context-item",role:"button",onClick:()=>__wx_channels_handle_click_download__()},"原始视频"),p("div",{class:"context-item",role:"button",onClick:__wx_channels_download_cur__},"当前视频"),p("div",{class:"context-item",role:"button",onClick:()=>__wx_channels_handle_download_cover()},"下载封面"),p("div",{class:"context-item",role:"button",onClick:__wx_channels_handle_copy__},"复制链接")]'''
            content = regex.sub(replace_str, content)
        return content

    def modify_js_4(self, path, content):
        if "worker_release" in path:
            # 打印主机名和路径
            print(f"拦截 {path}")
            # 正则表达式替换
            regex = re.compile(r'fmp4Index:p\.fmp4Index')
            replace_str = 'decryptor_array:p.decryptor_array,fmp4Index:p.fmp4Index'
            content = regex.sub(replace_str, content)
        return content


addons = [WeixinChannelInterceptor()]
