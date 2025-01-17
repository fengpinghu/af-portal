from portal import app, auth, logger, connect, jupyterlab, admin
from flask import session, request, render_template, url_for, redirect, jsonify, flash
import globus_sdk
from urllib.parse import urlparse, urljoin
from portal.jupyterlab import JupyterLabException

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/hardware")
def hardware():
    return render_template("hardware.html")

@app.route("/hardware/gpus")
def get_gpus():
    try:
        gpus = jupyterlab.get_gpus()
        return jsonify(gpus=gpus)
    except Exception as err:
        logger.error(str(err))
        return jsonify(gpus=[], error="There was an error getting GPU product information.")

@app.route("/signup")
def signup():
    return render_template("signup.html")

@app.route("/login")
def login():
    try:
        redirect_uri = url_for("login", _external=True)
        client = globus_sdk.ConfidentialAppAuthClient(app.config["CLIENT_ID"], app.config["CLIENT_SECRET"])
        client.oauth2_start_flow(redirect_uri, refresh_tokens=True)
        if "code" not in request.args:
            next_url = get_safe_redirect()
            params = {"signup": 1} if request.args.get("signup") else {"next": next_url}
            auth_uri = client.oauth2_get_authorize_url(additional_params=params)
            return redirect(auth_uri)
        else:
            code = request.args.get("code")
            tokens = client.oauth2_exchange_code_for_tokens(code)
            id_token = tokens.decode_id_token(client)
            session.update(
                tokens=tokens.by_resource_server, 
                is_authenticated=True,
                name=id_token.get("name", ""),
                email=id_token.get("email", ""),
                institution=id_token.get("organization", ""),
                globus_id=id_token.get("sub", ""),
                last_authentication=id_token.get("last_authentication", -1)
            )
            user = connect.find_user(session["globus_id"])
            if user:
                session["unix_name"] = user["unix_name"]
                session["role"] = connect.get_user_role(session["unix_name"])
            return redirect(url_for("home"))
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/logout")
@auth.login_required
def logout():
    try:
        client = globus_sdk.ConfidentialAppAuthClient(app.config["CLIENT_ID"], app.config["CLIENT_SECRET"])
        for token in (token_info["access_token"] for token_info in session["tokens"].values()):
            client.oauth2_revoke_token(token)
        session.clear()
        redirect_uri = url_for("home", _external=True)
        globus_logout_url = ("https://auth.globus.org/v2/web/logout?client=%s&redirect_uri=%s&redirect_name=AF Portal" %(app.config["CLIENT_ID"], redirect_uri))
        return redirect(globus_logout_url)
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

def is_safe_redirect_url(target):
  host_url = urlparse(request.host_url)
  redirect_url = urlparse(urljoin(request.host_url, target))
  return redirect_url.scheme in ('http', 'https') and \
    host_url.netloc == redirect_url.netloc

def get_safe_redirect():
  url =  request.args.get('next')
  if url and is_safe_redirect_url(url):
    return url
  url = request.referrer
  if url and is_safe_redirect_url(url):
    return url
  return '/'

@app.route("/profile")
@auth.login_required
def profile():
    try:
        unix_name = session["unix_name"]
        profile = connect.get_user_profile(unix_name)
        return render_template("profile.html", profile=profile)
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/profile/edit", methods=["GET", "POST"])
@auth.login_required
def edit_profile():
    try:
        unix_name = session["unix_name"]
        if request.method == "GET":
            profile = connect.get_user_profile(unix_name)
            return render_template("edit_profile.html", profile=profile)
        if request.method == "POST":
            name = request.form["name"]
            phone = request.form["phone"]
            institution = request.form["institution"]
            email = request.form["email"]
            x509_dn = request.form["X.509_DN"]
            public_key = request.form["public_key"]
            connect.update_user_profile(unix_name, name=name, phone=phone, institution=institution, email=email, x509_dn=x509_dn, public_key=public_key)
            return redirect(url_for("profile"))
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/profile/groups")
@auth.login_required
def user_groups():
    return render_template("user_groups.html")

@app.route("/profile/get_user_groups")
@auth.login_required
def get_user_groups():
    try:
        unix_name = session["unix_name"]
        groups = connect.get_user_groups(unix_name)
        return jsonify(groups=groups)
    except Exception as err:
        logger.error(str(err))
        return jsonify(groups=[], error="There was an error getting user groups.")

@app.route("/profile/request_membership/<unix_name>")
@auth.login_required
def request_membership(unix_name):
    try:
        if connect.update_user_group_status(unix_name, "root.atlas-af", "pending"):
            flash("Requested membership in the ATLAS Analysis Facility group", "success")
        else:
            flash("Unable to request membership in the ATLAS Analysis Facility group", "warning")
        return redirect(url_for("profile"))
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/aup")
def aup():
    return render_template("aup.html")

@app.route("/jupyterlab")
@auth.members_only
def open_jupyterlab():
    return render_template("jupyterlab.html")

@app.route("/jupyterlab/get_notebooks")
@auth.members_only
def get_notebooks():
    try:
        username = session["unix_name"]
        notebooks = jupyterlab.get_notebooks(username)
        return jsonify(notebooks=notebooks)
    except Exception as err:
        logger.error(str(err))
        return jsonify(notebooks=[], error="There was an error getting user notebooks.")

@app.route("/jupyterlab/configure")
@auth.members_only
def configure_notebook():
    try:
        username = session["unix_name"]
        notebook_name = jupyterlab.generate_notebook_name(username)
        return render_template("jupyterlab_form.html", notebook_name=notebook_name)
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/jupyterlab/deploy", methods=["POST"])
@auth.members_only
def deploy_notebook():
    try:
        notebook_name = request.form["notebook-name"].strip()
        settings = {
            "globus_id": session["globus_id"],
            "username": session["unix_name"],
            "cpu_request": int(request.form["cpu"]),
            "cpu_limit": int(request.form["cpu"])*2,
            "memory_request": int(request.form['memory']),
            "memory_limit": int(request.form['memory'])*2,  
            "gpu_request": int(request.form['gpu']),
            "gpu_limit": int(request.form['gpu']),
            "gpu_memory": int(request.form['gpu-memory']),
            "image": request.form['image'],
            "duration": int(request.form['duration'])}
        jupyterlab.create_notebook(notebook_name, **settings)
    except JupyterLabException as err:
        flash(str(err), "warning")
        return redirect(url_for("configure_notebook"))
    return redirect(url_for("open_jupyterlab"))

@app.route("/jupyterlab/remove/<notebook>")
@auth.members_only
def remove_notebook(notebook):
    try:
        pod = jupyterlab.get_pod(notebook)
        if pod.metadata.labels["owner"] == session["unix_name"]: 
            jupyterlab.remove_notebook(notebook)
            return jsonify(success=True, message="Notebook %s was deleted." %notebook)
    except Exception as err:
        logger.error(str(err))
    return jsonify(success=False)

@app.route("/kibana")
@auth.members_only
def kibana_user():
    try: 
        username = session["unix_name"]
        notebooks = jupyterlab.get_notebooks(username)
        return render_template("kibana_user.html", notebooks=notebooks)
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/admin/notebooks")
@auth.admins_only
def open_notebooks():
    return render_template("notebooks.html")

@app.route("/admin/list_notebooks")
@auth.admins_only
def list_notebooks():
    try:
        notebooks = jupyterlab.list_notebooks()
        return jsonify(notebooks=notebooks)
    except Exception as err:
        logger.error(str(err))
        return jsonify(notebooks=[], error="There was an error listing notebooks.")

@app.route("/admin/get_notebook/<notebook_name>")
@auth.admins_only
def get_notebook(notebook_name):
    try:
        notebook = jupyterlab.get_notebook(notebook_name)
        return jsonify(notebook=notebook)
    except Exception as err:
        logger.error(str(err))
        return jsonify(notebook=None, error="There was an error getting notebook %s." %notebook_name)    

@app.route("/admin/users")
@auth.admins_only
def user_info():
    return render_template("users.html")

@app.route("/admin/get_user_profiles")
@auth.admins_only
def get_user_profiles():
    try:
        usernames = connect.get_group_members("root.atlas-af")
        users = connect.get_user_profiles(usernames, date_format="%m/%d/%Y")
        return jsonify(users=users)
    except Exception as err:
        logger.error(str(err))
        return jsonify(error="There was an error getting user profiles.")

@app.route("/admin/update_user_institution", methods=["POST"])
@auth.admins_only
def update_user_institution():
    try:
        username = request.form['username']
        institution = request.form['institution']
        if connect.update_user_institution(username, institution):
            return jsonify(success=True, message="Updated user institution")
        return jsonify(success=False, message="Unable to update user institution") 
    except Exception as err:
        logger.error(str(err))
        return jsonify(success=False, message="There was an error updating the user institution")

@app.route("/admin/plot_users_over_time")
@auth.admins_only
def plot_users_over_time():
    try:
        data = admin.plot_users_over_time()
        return render_template("plot_users_over_time.html", base64_encoded_image = data)
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/admin/kibana")
@auth.admins_only
def kibana_admin():
    try: 
        notebooks = jupyterlab.get_notebooks()
        return render_template("kibana_admin.html", notebooks=notebooks)
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/admin/groups/<group_name>")
@auth.admins_only
def groups(group_name):
    try:
        group = connect.get_group_info(group_name)
        return render_template("groups.html", group=group)
    except Exception as err:
        logger.error(str(err))
        return render_template("404.html")

@app.route("/admin/get_group_members/<group_name>")
@auth.admins_only
def get_group_members(group_name):
    try:
        usernames = connect.get_group_members(group_name, states=["active", "admin"])
        profiles = connect.get_user_profiles(usernames)
        return jsonify(members=profiles)
    except Exception as err:
        logger.error(str(err))
        return jsonify(error="There was an error getting member profiles.")

@app.route("/admin/get_group_member_requests/<group_name>")
@auth.admins_only
def get_group_member_requests(group_name):
    try:
        usernames = connect.get_group_members(group_name, states=["pending"])
        profiles = connect.get_user_profiles(usernames)
        return jsonify(member_requests=profiles)
    except Exception as err:
        logger.error(str(err))
        return jsonify(error="There was an error getting member requests.")

@app.route("/admin/get_subgroups/<group_name>")
@auth.admins_only
def get_group_subgroups(group_name):
    try:
        subgroups = connect.get_subgroups(group_name)
        return jsonify(subgroups=subgroups)
    except Exception as err:
        logger.error(str(err))
        return jsonify(error="There was an error getting subgroups.")

@app.route("/admin/get_subgroup_requests/<group_name>")
@auth.admins_only
def get_group_subgroup_requests(group_name):
    try:
        subgroup_requests = connect.get_subgroup_requests(group_name)
        return jsonify(subgroup_requests=subgroup_requests)
    except Exception as err:
        logger.error(str(err))
        return jsonify(error="There was an error getting subgroup requests.")

@app.route("/admin/get_potential_members/<group_name>")
@auth.admins_only
def get_group_potential_members(group_name):
    try:
        members = connect.get_group_members(group_name, states=["admin", "active"])
        users = connect.get_group_members("root")
        potential_members = filter(lambda user : user not in members, users)
        profiles = connect.get_user_profiles(potential_members)
        return jsonify(potential_members=profiles)
    except Exception as err:
        logger.error(str(err))
        return jsonify(error="There was an error getting potential members.")

@app.route("/admin/email/<group_name>", methods=["POST"])
@auth.admins_only
def send_email(group_name):
    try:
        sender = "noreply@af.uchicago.edu"
        recipients = admin.get_email_list(group_name)
        subject = request.form["subject"]
        body = request.form["body"]
        if admin.email_users(sender, recipients, subject, body):
            return jsonify(success=True, message="Sent email to group %s" %group_name)
        return jsonify(success=False, message="Unable to send email to group %s" %group_name)
    except Exception as err:
        logger.error(str(err))
        return jsonify(success=False, message="Error sending email to group %s" %group_name)

@app.route("/admin/add_group_member/<group_name>/<unix_name>")
@auth.admins_only
def add_group_member(unix_name, group_name):
    try:
        success = connect.add_user_to_group(unix_name, group_name)
        return jsonify(success=True)
    except Exception as err:
        logger.error(str(err))
        return jsonify(success=True)

@app.route("/admin/remove_group_member/<group_name>/<unix_name>")
@auth.admins_only
def remove_group_member(unix_name, group_name):
    try:
        success = connect.remove_user_from_group(unix_name, group_name)
        return jsonify(success=True)
    except Exception as err:
        logger.error(str(err))
        return jsonify(success=False)

@app.route("/admin/approve_membership_request/<group_name>/<unix_name>")
@auth.admins_only
def approve_membership_request(unix_name, group_name):
    try:
        success = connect.add_user_to_group(unix_name, group_name)
        profile = connect.get_user_profile(unix_name)
        approver = session["unix_name"]
        subject = "Account approval"
        body = """
            User %s approved a request from %s to join group %s.

            Unix name: %s
            Full name: %s
            Email: %s
            Institution: %s""" %(approver, unix_name, group_name, profile["unix_name"], profile["name"], profile["email"], profile["institution"])
        admin.email_staff(subject, body)
        return jsonify(success=True)
    except Exception as err:
        logger.error(str(err))
        return jsonify(success=False)

@app.route("/admin/deny_membership_request/<group_name>/<unix_name>")
@auth.admins_only
def deny_membership_request(unix_name, group_name):
    try:
        success = connect.remove_user_from_group(unix_name, group_name)
        return jsonify(success=True)
    except Exception as err:
        logger.error(str(err))
        return jsonify(success=False)

@app.route("/admin/approve_subgroup_request/<group_name>/<subgroup_name>")
@auth.admins_only
def approve_subgroup_request(subgroup_name, group_name):
    try:
        success = connect.approve_subgroup_request(subgroup_name, group_name)
        flash("Approved request for subgroup %s" %subgroup_name, "success")
        return redirect(url_for("groups", group_name=group_name))
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/admin/deny_subgroup_request/<group_name>/<subgroup_name>")
@auth.admins_only
def deny_subgroup_request(subgroup_name, group_name):
    try:
        success = connect.deny_subgroup_request(subgroup_name, group_name)
        flash("Denied request for subgroup %s" %subgroup_name, "success")
        return redirect(url_for("groups", group_name=group_name))
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/admin/edit_group/<group_name>", methods=["GET", "POST"])
@auth.admins_only
def edit_group(group_name):
    try:
        group = connect.get_group_info(group_name)
        if request.method == "GET":
            return render_template("edit_group.html", group=group)
        elif request.method == "POST":
            kwargs = {
                "display_name": request.form["display-name"],
                "email": request.form["email"],
                "phone": request.form["phone"],
                "description": request.form["description"]
            }
            connect.update_group_info(group_name, **kwargs)
            flash("Updated group %s successfully" %group_name, "success")
            return redirect(url_for("groups", group_name=group_name))
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/admin/create_subgroup/<group_name>", methods=["GET", "POST"])
@auth.admins_only
def create_subgroup(group_name):
    try:
        group = connect.get_group_info(group_name)
        if request.method == "GET":
            return render_template("create_subgroup.html", group=group)
        elif request.method == "POST":
            subgroup_name = request.form["short-name"]
            kwargs = {
                "name": subgroup_name,
                "display_name": request.form["display-name"],
                "purpose": request.form["purpose"],
                "email": request.form["email"],
                "phone": request.form["phone"],
                "description": request.form["description"]
            }
            success = connect.create_subgroup(subgroup_name, group_name, **kwargs)
            if success:
                flash("Created subgroup %s" %subgroup_name, "success")
            else:
                flash("Error creating subgroup %s" %subgroup_name, "warning")
            return redirect(url_for("groups", group_name=group_name))
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/admin/delete_group/<group_name>")
@auth.admins_only
def delete_group(group_name):
    try:
        if connect.is_group_deletable(group_name):
            success = connect.delete_group(group_name)
            if success:
                flash("Deleted group %s" %group_name, "success")
            else:
                flash("Error deleting group %s" %group_name, "warning")
        return redirect(url_for("groups", group_name="root.atlas-af"))
    except Exception as err:
        logger.error(str(err))
        return render_template("500.html")

@app.route("/admin/login_nodes")
@auth.admins_only
def login_nodes():
    return render_template("login_nodes.html")

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html")

@app.errorhandler(500)
def internal_server_error(e):
    return render_template("500.html")