from flask import Flask, render_template, request, redirect, url_for, jsonify
import boto3
from config import *
from helper import *
import base64
from flask import jsonify


app = Flask(__name__)

s3_bucket = S3_BUCKET
aws_region = AWS_REGION
aws_access_key_id =AWS_ACCESS_KEY_ID
aws_secret_access_key = AWS_SECRET_ACCESS_KEY

rds_params = {
    "host": RDS_HOST,
    "user": RDS_USER,
    "port": 3306,
    "password": RDS_PASSWORD,
    "db": RDS_DB,
}

output = {}
table = "registration_table"

db_conn = establish_connection(rds_params)

def search_student_record(connection,sid):
    cursor = connection.cursor()
    try:
        query = "SELECT * FROM registration_table WHERE sid = %s"
        cursor.execute(query, (sid,))
        result = cursor.fetchone()
        print("Result is:", result)
        return result
    finally:
        cursor.close()   
        
import boto3

def retrieve_image_from_s3(sid, bucket_name, aws_access_key_id, aws_secret_access_key):
    """
    Retrieve an image from an S3 bucket based on the student ID.

    Args:
    - sid (str): The ID of the student.
    - bucket_name (str): The name of the S3 bucket.
    - aws_access_key_id (str): The AWS access key ID.
    - aws_secret_access_key (str): The AWS secret access key.

    Returns:
    - image_data (bytes or None): The raw bytes of the image data, or None if image retrieval fails.
    """
    # Initialize the S3 client
    s3 = boto3.client('s3', aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key)

    # Construct the key (filename) based on the student ID
    possible_extensions = ['jpeg', 'jpg', 'png']
    for ext in possible_extensions:
        key = f"{sid}.{ext}"

        try:
            # Retrieve the image object from S3
            response = s3.get_object(Bucket=bucket_name, Key=key)

            # Read the image data
            image_data = response['Body'].read()
            return image_data

        except s3.exceptions.NoSuchKey:
            # If the image with current extension does not exist, try next extension
            continue

        except Exception as e:
            # Handle exceptions (e.g., if the image doesn't exist)
            print(f"Error retrieving image for student ID {sid}: {e}")
            return None

    # Return None if no valid image found
    return None


def send_ses_verification_mail(ses_client,email):

    # Send verification email
    response = ses_client.verify_email_identity(EmailAddress=email)

    print("Verification email sent. Check your inbox.")
    return response

def check_email_verification_status(ses_client,email):
    # Get email verification status
    response = ses_client.get_identity_verification_attributes(Identities=[email])

    # Check if verification status is 'Success'
    verification_status = response['VerificationAttributes'][email]['VerificationStatus']
    return verification_status == 'Success'

def generate_sid():

    cursor = db_conn.cursor()

    try:
        # Get the last row index and increment
        cursor.execute("SELECT MAX(row_index) FROM registration_table")
        last_row_index = cursor.fetchone()[0]
        print("This is the last record", last_row_index)
        next_row_index = last_row_index + 1 if last_row_index is not None else 1
        # Generate the sid
        sid = f"sid_{next_row_index}"
        print("sid is:", sid)
        return sid
    finally:
        cursor.close()
        
# Initialize AWS SES client
ses_client = boto3.client("ses", region_name="ap-southeast-2")


@app.route("/", methods=["GET", "POST"])
def index():
    return render_template("home.html")


@app.route("/verification_form", methods=["GET", "POST"])
def verification_form():
    return render_template("verification.html")


@app.route("/verification", methods=["GET", "POST"])
def verification():
    print("Entering into verification")
    if request.method == "POST":
        print("Verification started")
        # Handle verification form submission
        ses_client = boto3.client('ses', region_name=aws_region)
        print("ses client created")
        email = request.form.get("email")
        print("email:",email)
        isVerified = check_email_verification_status(ses_client,email)
        if isVerified == False:
            response = send_ses_verification_mail(ses_client,email)
            print("Response for the verification mail sent for ses",response)
        elif isVerified == True:
            print(f"The email address {email} is verified.")
            return redirect(url_for("registration"))
        else:
            # Handle error if email verification fails
            print(f"The email address {email} is not verified due to some error.")
            # alert(f"Your email address {email} is not verified.")
            return render_template("verification.html")
              
    return render_template("verification.html")


@app.route("/registration", methods=["GET", "POST"])
def registration():
    return render_template("registration.html")

@app.route("/registered_data", methods=["GET", "POST"])
def registration_data():
    if request.method == "POST":
        try:
            first_name = request.form["first_name"]
            last_name = request.form["last_name"]
            email = request.form["email"]
            mobile_number = request.form["mobile_number"]
            location = request.form["location"]
            image_option = request.form["imageOption"]
            sid = generate_sid()

            if image_option == "upload":
                image = request.files["image"]
                img_extension = image.filename.split(".")[-1]
                image_data = image.read()
            elif image_option == "capture":
                image_data_base64 = request.form["capturedImageData"]
                image_data = base64.b64decode(image_data_base64.split(",")[1])
                img_extension = "jpeg"

            insert_sql = "INSERT INTO registration_table (sid, first_name, last_name, email, mobile_number, location) VALUES (%s, %s, %s, %s, %s, %s)"
            with db_conn.cursor() as cursor:
                cursor.execute(insert_sql, (sid, first_name, last_name, email, mobile_number, location))
                db_conn.commit()

                insert_attendance_sql = "INSERT INTO attendance_table (sid, entry_date) VALUES (%s, CURDATE())"
                cursor.execute(insert_attendance_sql, (sid,))
                db_conn.commit()

                s3_image_filename = f"{sid}.{img_extension}"
                s3 = boto3.resource("s3")
                s3.Bucket(s3_bucket).put_object(Key=s3_image_filename, Body=image_data)

                bucket_location = boto3.client("s3").get_bucket_location(Bucket=s3_bucket)
                s3_location = bucket_location["LocationConstraint"]
                if s3_location is None:
                    s3_location = ""
                else:
                    s3_location = "-" + s3_location

                object_url = f"https://s3{s3_location}.amazonaws.com/{s3_bucket}/{s3_image_filename}"
                dynamodb_client = boto3.client("dynamodb", region_name=aws_region)
                dynamodb_client.put_item(
                    TableName="student_image_table",
                    Item={"sid": {"S": sid}, "image_url": {"S": object_url}},
                )
                return redirect(url_for("index"))
        except Exception as e:
            return str(e)
    return render_template("registration.html")

@app.route("/search_student")
def search_student():
    return render_template("search_student.html")
       
         
@app.route("/student_record", methods=["POST"])
def student_record():
    if request.method == "POST":
        sid = request.form["sid"]
        student_record_tuple = search_student_record(db_conn, sid)

        if student_record_tuple:
            # Construct dictionary from the tuple
            record_fields = ["ID", "Student ID", "First Name", "Last Name", "Email", "Phone", "Location"]
            student_record_dict = {field: value for field, value in zip(record_fields, student_record_tuple)}

            # Retrieve image from S3
            image_data = retrieve_image_from_s3(sid, s3_bucket, aws_access_key_id, aws_secret_access_key)
            image_base64 = None  # Initialize image_base64

            if image_data:
                # Convert image data to base64
                image_base64 = base64.b64encode(image_data).decode('utf-8')

            return render_template("student_record.html", student_record=student_record_dict, image_base64=image_base64)
        else:
            # If student record not found, render template with error message or handle accordingly
            error_message = "Student record not found."
            return render_template("student_record.html", error_message=error_message)
    else:
        return render_template("search_student.html")

    
@app.route("/attendance_record_form", methods=["GET", "POST"])
def attendance_record_form():
    return render_template("attendance_record_form.html")
        
        

@app.route("/get_attendance", methods=["POST"])
def get_attendance():
    if request.method == "POST":  
        query_type = request.form["query_type"]
        print("Query Type:", query_type)
        
        if query_type == "view dates when the student was present /absent":
            sid = request.form["student_id"]
            status = request.form["status"]
           
            if sid and status:
                cursor = db_conn.cursor()
                query = "SELECT DATE(entry_date) AS entry_date FROM attendance_table WHERE sid = %s AND status = %s"
                cursor.execute(query, (sid, status))
                results = cursor.fetchall()
                cursor.close()

                # Convert entry_date values to strings representing date only
                formatted_results = [[row[0]] for row in results]

                print("Result", formatted_results)
                return render_template("status_based.html", dates=formatted_results)
                
            else:
                print("Data is unavailable")
                
        if query_type == "view the attendance status of a student for particular date":
            sid = request.form["student_id"]
            date = request.form["date"]
            
            if sid and date:
                cursor = db_conn.cursor()
                query = "SELECT DATE(entry_date) AS entry_date, status FROM attendance_table WHERE sid = %s AND entry_date = %s"
                cursor.execute(query, (sid, date))
                result = cursor.fetchone()
                cursor.close()
                
                if result:
                    # Render the template with the fetched data
                    return render_template("date_based.html", result=result)
                else:
                    # Handle case where no data is found
                    return jsonify({"error": "Data not found for the specified student and date"}), 404
            else:
                print("Data is unavailable")
                return jsonify({"error": "Missing parameters"}), 400
            
            
        if query_type == "view attendance of all the students for particular date":
            date = request.form["date"]
            
            if date:
                cursor = db_conn.cursor()
                query = "SELECT sid, status FROM attendance_table WHERE entry_date = %s"
                cursor.execute(query, (date))
                result = cursor.fetchall()
                cursor.close()
            
                if result:
                    # Render the template with the fetched data
                    return render_template("allstudents_date_based.html", result=result,date=date)
                else:
                    # Handle case where no data is found
                    return jsonify({"error": "Data not found for the specified student and date"}), 404
                
        if query_type == "view monthly attendance data for all the students":
            month = request.form["month"]
            year = request.form["year"]
            
            
            if month and year:
                cursor = db_conn.cursor()
                query = "SELECT sid, entry_date, status FROM attendance_table WHERE MONTH(entry_date) = %s AND YEAR(entry_date) = %s"
                cursor.execute(query, (month, year))
                result = cursor.fetchall()
                cursor.close()
                
                if result:
                    # Render the template with the fetched data
                    return render_template("month_year_based.html", result=result, month=month, year=year)
                else:
                    # Handle case where no data is found
                    return jsonify({"error": "Data not found for the specified student and date"}), 404
                        
    return jsonify({"error": "Invalid request"}), 400  # Returning error JSON response with status code 400

@app.route("/registered_students", methods=["GET", "POST"])
def registered_students():
    if request.method == "GET":  
        cursor = db_conn.cursor()
        query = "SELECT * FROM registration_table"
        cursor.execute(query)
        results = cursor.fetchall()
        cursor.close()
        print("Results:",results)
        return render_template("registration_table_based.html", results=results)
        
    else:
        cursor.close()
        # Handle case where no data is found
        return jsonify({"error": "Data not found"}), 404

@app.route("/admin", methods=["GET", "POST"])
def admin():
    return render_template("admin_login.html")
       
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        if email == admin_credentials['email'] and password == admin_credentials['password']:
            # Successful login, redirect to admin portal
            return render_template('admin_portal.html')
        else:
            # Failed login attempt
            return render_template('admin_login.html', message='Invalid credentials. Please try again.')

    # For GET request or initial load of the page
    return render_template('admin_login.html')

                
if __name__ == '__main__': 
    app.run(host='0.0.0.0',port=80,debug=True)